"""Versioned calibration identity as a hard pointer-action gate.

The exact client-size brake was only an emergency calibration check. A
profile-calibrated pointer click depends on the declared host identity. This gate makes
every one of those an explicit, observed fact: a value that cannot be read is
`unknown` and blocks input, never a silent match.
"""

from __future__ import annotations

from kenshi_agent.control.calibration import (
    calibration_allows_input,
    evaluate_calibration_identity,
)
from kenshi_agent.core.operation import PointerActionClass
from kenshi_agent.core.transport import (
    CalibrationIdentity,
    CalibrationStatus,
)


def full_identity(**overrides: object) -> CalibrationIdentity:
    base = {
        "client_width": 1920,
        "client_height": 1080,
        "window_mode": "borderless",
        "ui_scale": 1.0,
        "dpi_scale": 1.25,
        "keymap_id": "default-v1",
        "profile_id": "hub-barman",
        "profile_version": 3,
        "macro_set_hash": "abc123",
    }
    base.update(overrides)
    return CalibrationIdentity(**base)  # type: ignore[arg-type]


def evaluate(
    *,
    action_class: PointerActionClass = PointerActionClass.PROFILE_CALIBRATED,
    expected: CalibrationIdentity | None,
    observed: CalibrationIdentity | None,
):
    return evaluate_calibration_identity(
        action_class=action_class,
        expected=expected,
        observed=observed,
    )


def test_matching_full_identity_allows_the_guarded_path() -> None:
    report = evaluate(expected=full_identity(), observed=full_identity())
    assert report.status is CalibrationStatus.MATCHED
    assert calibration_allows_input(report) is True
    assert report.mismatched_fields == []
    assert report.unobserved_fields == []


def test_coordinate_independent_action_never_requires_calibration() -> None:
    report = evaluate(
        action_class=PointerActionClass.COORDINATE_INDEPENDENT,
        expected=None,
        observed=None,
    )
    assert report.status is CalibrationStatus.NOT_REQUIRED
    assert calibration_allows_input(report) is True


def test_semantic_current_action_is_resolution_independent() -> None:
    # A mismatched profile must not block a semantic action, since it resolves
    # live bounds re-read inside the lease rather than replaying coordinates.
    report = evaluate(
        action_class=PointerActionClass.SEMANTIC_CURRENT,
        expected=full_identity(),
        observed=full_identity(client_width=1280, client_height=720),
    )
    assert report.status is CalibrationStatus.NOT_REQUIRED
    assert calibration_allows_input(report) is True


def test_unsupported_action_class_is_never_allowed() -> None:
    report = evaluate(
        action_class=PointerActionClass.UNSUPPORTED,
        expected=full_identity(),
        observed=full_identity(),
    )
    assert report.status is CalibrationStatus.MISMATCHED
    assert calibration_allows_input(report) is False


def test_missing_expected_identity_blocks_as_unknown() -> None:
    report = evaluate(expected=CalibrationIdentity(), observed=full_identity())
    assert report.status is CalibrationStatus.UNKNOWN
    assert calibration_allows_input(report) is False


def test_missing_observed_identity_blocks_as_unknown() -> None:
    report = evaluate(expected=full_identity(), observed=None)
    assert report.status is CalibrationStatus.UNKNOWN
    assert calibration_allows_input(report) is False
    assert set(report.unobserved_fields) == set(full_identity().declared_fields())


def test_each_declared_field_mismatch_blocks_input() -> None:
    mismatches = {
        "client_width": 1280,
        "client_height": 720,
        "window_mode": "fullscreen",
        "ui_scale": 1.25,
        "dpi_scale": 1.0,
        "keymap_id": "remapped-v2",
        "profile_id": "other-profile",
        "profile_version": 4,
        "macro_set_hash": "def456",
    }
    for field, bad_value in mismatches.items():
        report = evaluate(
            expected=full_identity(),
            observed=full_identity(**{field: bad_value}),
        )
        assert report.status is CalibrationStatus.MISMATCHED, field
        assert report.mismatched_fields == [field], field
        assert calibration_allows_input(report) is False, field


def test_unobserved_declared_field_is_unknown_not_matched() -> None:
    # The core invariant: a null observed value is not agreement. An unread UI
    # scale must not be treated as the expected UI scale.
    report = evaluate(
        expected=full_identity(),
        observed=full_identity(ui_scale=None),
    )
    assert report.status is CalibrationStatus.UNKNOWN
    assert report.unobserved_fields == ["ui_scale"]
    assert calibration_allows_input(report) is False


def test_only_declared_fields_are_compared() -> None:
    # The profile declares just client size; the host observes far more. The
    # undeclared extras must neither block nor be required.
    expected = CalibrationIdentity(client_width=1920, client_height=1080)
    observed = full_identity()
    report = evaluate(expected=expected, observed=observed)
    assert report.status is CalibrationStatus.MATCHED
    assert calibration_allows_input(report) is True


def test_float_fields_match_within_tolerance() -> None:
    report = evaluate(
        expected=full_identity(ui_scale=1.0, dpi_scale=1.25),
        observed=full_identity(ui_scale=1.0 + 1e-9, dpi_scale=1.25 - 1e-9),
    )
    assert report.status is CalibrationStatus.MATCHED


def test_unknown_takes_precedence_over_mismatch_in_reason() -> None:
    # When a field is both unreadable elsewhere and another mismatches, the
    # conservative UNKNOWN status wins so nothing is reported as a clean block.
    report = evaluate(
        expected=full_identity(),
        observed=full_identity(ui_scale=None, window_mode="fullscreen"),
    )
    assert report.status is CalibrationStatus.UNKNOWN
    assert "ui_scale" in report.unobserved_fields
    assert "window_mode" in report.mismatched_fields
    assert calibration_allows_input(report) is False


def test_mismatched_reason_names_expected_and_observed() -> None:
    report = evaluate(
        expected=full_identity(profile_version=3),
        observed=full_identity(profile_version=9),
    )
    assert "profile_version" in report.reason
    assert "3" in report.reason and "9" in report.reason
