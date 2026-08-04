"""Thin console adapter for the public application composition root."""

from __future__ import annotations

from .application import main as application_main
from .tooling.scenario_fixtures import load_verified_scenario_attestation


def main(argv: list[str] | None = None) -> int:
    return application_main(
        argv,
        scenario_proof_loader=load_verified_scenario_attestation,
    )
