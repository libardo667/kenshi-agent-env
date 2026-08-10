from __future__ import annotations

from kenshi_agent.tooling.ui_affordances import AFFORDANCES, Operation, audit


def test_every_modeled_interface_exit_is_implemented() -> None:
    report = audit()

    assert report.stranding_gaps == ()
    assert all(
        row.covered
        for row in AFFORDANCES
        if row.operation is Operation.EXIT
    )
