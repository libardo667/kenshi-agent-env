from __future__ import annotations

from pathlib import Path

import pytest

from kenshi_agent.affordance_parity import (
    CONTROLS_SNAPSHOT,
    BindingStatus,
    audit_binding_parity,
    load_controls_cfg,
    parse_controls_cfg,
)

INSTALLED_CONTROLS = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Kenshi/controls.cfg"
)
MAX_KNOWN_MISSING_BINDINGS = 41


def test_controls_parser_preserves_alternates_and_equals_key() -> None:
    source = parse_controls_cfg(
        "1\n"
        "build_move_up==\n"
        "camera_back=S\n"
        "camera_back=Down\n"
    )

    assert source.version == 1
    assert [(binding.name, binding.inputs) for binding in source.bindings] == [
        ("build_move_up", ("=",)),
        ("camera_back", ("S", "Down")),
    ]


def test_every_game_binding_has_one_explicit_decision() -> None:
    report = audit_binding_parity()

    assert not report.unclassified(), (
        "Kenshi bindings absent from the parity ledger: "
        f"{report.unclassified()}. Wire each one or exempt it with a typed reason."
    )
    assert not report.stale_decisions(), (
        "Parity decisions no longer present in Kenshi's source: "
        f"{report.stale_decisions()}."
    )
    assert not report.decision_errors()


def test_a_new_game_binding_cannot_disappear_inside_our_own_denominator() -> None:
    expanded = parse_controls_cfg(
        CONTROLS_SNAPSHOT.read_text(encoding="utf-8")
        + "\nnew_human_affordance=F10\n"
    )

    report = audit_binding_parity(expanded)

    assert report.unclassified() == ("new_human_affordance",)


def test_known_missing_queue_only_shrinks() -> None:
    report = audit_binding_parity()
    missing = report.with_status(BindingStatus.MISSING)

    assert len(missing) <= MAX_KNOWN_MISSING_BINDINGS
    assert len(report.with_status(BindingStatus.WIRED)) == 24
    assert len(report.with_status(BindingStatus.EXEMPT)) == 7


@pytest.mark.skipif(
    not INSTALLED_CONTROLS.is_file(),
    reason="Kenshi controls.cfg is unavailable on this host",
)
def test_captured_binding_denominator_matches_installed_kenshi() -> None:
    captured = load_controls_cfg()
    installed = load_controls_cfg(INSTALLED_CONTROLS)

    assert captured.version == installed.version
    assert captured.names == installed.names
