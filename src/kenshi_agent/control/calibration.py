from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    CalibrationIdentity,
    CalibrationReport,
    CalibrationStatus,
    PointerActionClass,
    Vec2,
)

# Fields compared as floats need a tolerance; the rest compare exactly.
_FLOAT_FIELDS = frozenset({"ui_scale", "dpi_scale"})
_FLOAT_TOLERANCE = 1e-6


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalibrationWindow(CalibrationModel):
    expected_width: int = Field(gt=0)
    expected_height: int = Field(gt=0)
    ui_scale: float = Field(default=1.0, gt=0.0)


class Calibration(CalibrationModel):
    version: int = 1
    window: CalibrationWindow
    anchors: dict[str, Vec2]

    @classmethod
    def load(cls, path: Path) -> Calibration:
        with path.open("r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle) or {})

    def validate_window(self, width: int, height: int, *, tolerance_px: int = 2) -> None:
        if (
            abs(width - self.window.expected_width) > tolerance_px
            or abs(height - self.window.expected_height) > tolerance_px
        ):
            raise ValueError(
                f"Client area {width}x{height} does not match calibration "
                f"{self.window.expected_width}x{self.window.expected_height}."
            )


def _values_match(field: str, expected: object, observed: object) -> bool:
    if field in _FLOAT_FIELDS:
        if not isinstance(expected, (int, float)) or not isinstance(observed, (int, float)):
            return False
        return abs(float(expected) - float(observed)) <= _FLOAT_TOLERANCE
    return expected == observed


def evaluate_calibration_identity(
    *,
    action_class: PointerActionClass,
    expected: CalibrationIdentity | None,
    observed: CalibrationIdentity | None,
) -> CalibrationReport:
    """Decide whether this action's coordinates are still meaningful.

    Only fields the expected profile actually declares are compared. A declared
    field that the host cannot currently observe is `unknown`, never a match:
    an unread UI scale is not evidence that the UI scale is correct.
    """

    if action_class is PointerActionClass.UNSUPPORTED:
        return CalibrationReport(
            status=CalibrationStatus.MISMATCHED,
            action_class=action_class,
            reason="The action's pointer class is unsupported, so no calibration can authorize it.",
            expected=expected,
            observed=observed,
        )

    if action_class is PointerActionClass.COORDINATE_INDEPENDENT:
        return CalibrationReport(
            status=CalibrationStatus.NOT_REQUIRED,
            action_class=action_class,
            reason="The action carries no screen coordinates, so calibration cannot affect it.",
            observed=observed,
        )

    if action_class is PointerActionClass.SEMANTIC_CURRENT:
        return CalibrationReport(
            status=CalibrationStatus.NOT_REQUIRED,
            action_class=action_class,
            reason=(
                "The action resolves current semantic bounds that are re-read inside "
                "the input lease, so it does not depend on a calibrated profile."
            ),
            observed=observed,
        )

    declared = expected.declared_fields() if expected is not None else ()
    if expected is None or not declared:
        return CalibrationReport(
            status=CalibrationStatus.UNKNOWN,
            action_class=action_class,
            reason=(
                "A profile-calibrated pointer action requires a declared calibration "
                "identity, and none is configured."
            ),
            expected=expected,
            observed=observed,
        )

    if observed is None:
        return CalibrationReport(
            status=CalibrationStatus.UNKNOWN,
            action_class=action_class,
            reason="No calibration identity could be observed for this host.",
            expected=expected,
            observed=None,
            unobserved_fields=list(declared),
        )

    unobserved = [field for field in declared if getattr(observed, field) is None]
    mismatched = [
        field
        for field in declared
        if getattr(observed, field) is not None
        and not _values_match(field, getattr(expected, field), getattr(observed, field))
    ]

    if unobserved:
        return CalibrationReport(
            status=CalibrationStatus.UNKNOWN,
            action_class=action_class,
            reason=(
                "Calibration identity is incomplete; these declared fields could not be "
                f"observed: {', '.join(unobserved)}."
            ),
            expected=expected,
            observed=observed,
            mismatched_fields=mismatched,
            unobserved_fields=unobserved,
        )

    if mismatched:
        details = ", ".join(
            f"{field} expected {getattr(expected, field)!r}, observed "
            f"{getattr(observed, field)!r}"
            for field in mismatched
        )
        return CalibrationReport(
            status=CalibrationStatus.MISMATCHED,
            action_class=action_class,
            reason=f"Calibration identity does not match: {details}.",
            expected=expected,
            observed=observed,
            mismatched_fields=mismatched,
        )

    return CalibrationReport(
        status=CalibrationStatus.MATCHED,
        action_class=action_class,
        reason=(
            f"Observed calibration identity matches all {len(declared)} declared "
            "profile fields."
        ),
        expected=expected,
        observed=observed,
    )


def calibration_allows_input(report: CalibrationReport) -> bool:
    return report.status in (CalibrationStatus.NOT_REQUIRED, CalibrationStatus.MATCHED)


def validate_expected_client_size(
    width: int,
    height: int,
    *,
    expected_width: int | None,
    expected_height: int | None,
) -> None:
    if expected_width is None and expected_height is None:
        return
    if expected_width is None or expected_height is None:
        raise RuntimeError("Calibrated client dimensions are incomplete.")
    if width != expected_width or height != expected_height:
        raise RuntimeError(
            f"Client area {width}x{height} does not match calibrated "
            f"{expected_width}x{expected_height}; no pointer input was sent."
        )
