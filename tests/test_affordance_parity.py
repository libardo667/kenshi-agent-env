from __future__ import annotations

from pathlib import Path

import pytest

from kenshi_agent.affordance_parity import (
    CONTROLS_SNAPSHOT,
    BindingStatus,
    ExemptionKind,
    audit_binding_parity,
    load_controls_cfg,
    parse_controls_cfg,
)

INSTALLED_CONTROLS = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Kenshi/controls.cfg"
)
MIN_WIRED_BINDINGS = 24
MAX_EXEMPT_BINDINGS = 1


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


def test_reach_only_grows_and_exemptions_only_shrink() -> None:
    """Ratchet the two numbers a decision can game, not the remainder.

    Ratcheting the missing queue downward looks right and punishes honesty: the
    queue grew from 41 to 47 the moment six exemptions asserting preference
    rather than constraint were reclassified as work, which is the direction we
    want. Wired can only rise and exempt can only fall, so `missing` is free to
    move as the honest remainder. Exempt is the number worth pinning hardest,
    because an exemption list is what a queue decays into when nobody is
    counting.
    """

    report = audit_binding_parity()

    assert len(report.with_status(BindingStatus.WIRED)) >= MIN_WIRED_BINDINGS
    assert len(report.with_status(BindingStatus.EXEMPT)) <= MAX_EXEMPT_BINDINGS
    assert (
        len(report.with_status(BindingStatus.WIRED))
        + len(report.with_status(BindingStatus.EXEMPT))
        + len(report.with_status(BindingStatus.MISSING))
        == len(report.decisions)
    )


def test_an_exemption_cannot_be_justified_by_preference() -> None:
    """The categories are the enforcement; care is not.

    `safety` and `debug_only` withheld quicksave, quickload and the four
    world-data bindings on grounds that were judgement about what the agent
    ought to want -- on saves this project designates disposable, on the
    operator's own development machine. Two agents produced that same list
    independently. Every surviving category has to name a property of this
    system that a reader could check.
    """

    assert {kind.value for kind in ExemptionKind} == {
        "superseded",
        "host_effect",
        "no_observable_effect",
    }

    report = audit_binding_parity()
    exempt = report.with_status(BindingStatus.EXEMPT)
    assert set(exempt) == {"screenshot"}
    for name in exempt:
        assert report.decisions[name].reason


def test_withdrawn_preference_exemptions_are_queued_or_wired_not_hidden() -> None:
    report = audit_binding_parity()
    missing = report.with_status(BindingStatus.MISSING)
    wired = report.with_status(BindingStatus.WIRED)
    exempt = report.with_status(BindingStatus.EXEMPT)

    for name in (
        "quicksave",
        "quickload",
        "editor_toggle",
        "editor_delete",
        "rebuild_navmesh",
        "reload_biomes",
    ):
        assert name not in exempt, f"{name} returned to a preference exemption"
        assert name in missing or name in wired, f"{name} is neither queued nor wired"
    # The technical objection to loading is causal, not moral, and it has to be
    # written where the next reader will find it.
    assert "session boundary" in report.decisions["quickload"].reason


@pytest.mark.skipif(
    not INSTALLED_CONTROLS.is_file(),
    reason="Kenshi controls.cfg is unavailable on this host",
)
def test_captured_binding_denominator_matches_installed_kenshi() -> None:
    captured = load_controls_cfg()
    installed = load_controls_cfg(INSTALLED_CONTROLS)

    assert captured.version == installed.version
    assert captured.names == installed.names
