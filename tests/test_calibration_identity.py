"""Versioned calibration identity as a hard pointer-action gate (P4).

The exact client-size brake was only an emergency calibration check. A
profile-calibrated pointer click actually depends on client size, window mode,
UI scale, DPI transform, keymap, and the calibrated macro set. This gate makes
every one of those an explicit, observed fact: a value that cannot be read is
`unknown` and blocks input, never a silent match.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from test_input_boundary import observation, paused_condition, selection_condition
from test_live_env import PulseController, PulseTelemetry, movement_action, movement_registry

from kenshi_agent.config import CaptureConfig, ControlsConfig, RuntimeConfig
from kenshi_agent.control.calibration import (
    calibration_allows_input,
    evaluate_calibration_identity,
)
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.models import (
    CalibrationIdentity,
    CalibrationStatus,
    CommandDispatchContext,
    ControlMode,
    InputBoundaryDecision,
    PointerActionClass,
    WorldStateRevision,
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


# --- Live-environment integration ---------------------------------------


class ScaledController(PulseController):
    """A controller that can report a full observed identity and drift on lease."""

    def __init__(
        self,
        telemetry: PulseTelemetry,
        *,
        ui_scale: float = 1.0,
        ui_scale_in_lease: float | None = None,
    ) -> None:
        super().__init__(telemetry)
        self.ui_scale = ui_scale
        self.ui_scale_in_lease = ui_scale_in_lease

    def observed_calibration_identity(self) -> CalibrationIdentity:
        return CalibrationIdentity(
            client_width=self.client_width,
            client_height=self.client_height,
            ui_scale=self.ui_scale,
        )

    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False):
        del alt_tab_on_restore
        if self.ui_scale_in_lease is not None:
            # The display changed while the agent waited for a quiet turn.
            self.ui_scale = self.ui_scale_in_lease
        yield


def scaled_environment(
    tmp_path: Path,
    telemetry: PulseTelemetry,
    controller: PulseController,
    *,
    expected_ui_scale: float | None = 1.0,
    semantic_skills: list[str] | None = None,
) -> LiveEnvironment:
    return LiveEnvironment(
        run_id="calib-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=movement_registry(),
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Explore nearby."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            calibrated_client_width=1920,
            calibrated_client_height=1080,
            calibrated_ui_scale=expected_ui_scale,
            semantic_pointer_skills=semantic_skills or [],
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["move_visible_terrain"],
    )


def test_ui_scale_mismatch_blocks_pointer_input_before_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = ScaledController(telemetry, ui_scale=1.25)
        env = scaled_environment(tmp_path, telemetry, controller, expected_ui_scale=1.0)
        await env.reset()

        raised = False
        try:
            await env.step(movement_action())
        except RuntimeError as exc:
            raised = "ui_scale" in str(exc)

        assert raised
        assert controller.actions == []

    asyncio.run(scenario())


def test_matching_ui_scale_executes_the_pointer_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = ScaledController(telemetry, ui_scale=1.0)
        env = scaled_environment(tmp_path, telemetry, controller, expected_ui_scale=1.0)
        await env.reset()

        transition = await env.step(movement_action())

        assert controller.actions  # the click plus its bounded pause keys
        report = transition.receipt.calibration
        assert report is not None
        assert report.status is CalibrationStatus.MATCHED
        assert report.action_class is PointerActionClass.PROFILE_CALIBRATED

    asyncio.run(scenario())


def test_semantic_skill_ignores_calibration_mismatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = ScaledController(telemetry, ui_scale=1.25)
        env = scaled_environment(
            tmp_path,
            telemetry,
            controller,
            expected_ui_scale=1.0,
            semantic_skills=["move_visible_terrain"],
        )
        await env.reset()

        transition = await env.step(movement_action())

        assert controller.actions
        report = transition.receipt.calibration
        assert report is not None
        assert report.action_class is PointerActionClass.SEMANTIC_CURRENT
        assert report.status is CalibrationStatus.NOT_REQUIRED

    asyncio.run(scenario())


def test_classify_pointer_action_buckets(tmp_path: Path) -> None:
    from kenshi_agent.models import KeyAction

    telemetry = PulseTelemetry()
    controller = ScaledController(telemetry)
    env = scaled_environment(
        tmp_path, telemetry, controller, semantic_skills=["move_visible_terrain"]
    )
    assert (
        env.classify_pointer_action(movement_action())
        is PointerActionClass.SEMANTIC_CURRENT
    )
    plain = scaled_environment(tmp_path, telemetry, controller)
    assert (
        plain.classify_pointer_action(movement_action())
        is PointerActionClass.PROFILE_CALIBRATED
    )
    assert (
        plain.classify_pointer_action(KeyAction(key="space"))
        is PointerActionClass.COORDINATE_INDEPENDENT
    )


def test_calibration_drift_inside_lease_is_caught_by_the_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        # Matches before the lease, drifts to 1.25 once the lease is acquired.
        controller = ScaledController(telemetry, ui_scale=1.0, ui_scale_in_lease=1.25)
        env = scaled_environment(tmp_path, telemetry, controller, expected_ui_scale=1.0)
        await env.reset()

        latest = [observation()]
        token = ExecutionToken(
            plan_id="calib-plan",
            plan_version=1,
            step_id="approach",
            command_id="cmd-" + "0" * 32,
            control_mode=ControlMode.INTERFACE_ONLY,
            validated_revision=WorldStateRevision(
                telemetry_sequence=10,
                capability_epoch=1,
                observed_at_monotonic=10.0,
            ),
            latest_observation=lambda: latest[0],
            assumptions=(paused_condition(),),
            preconditions=(selection_condition(),),
        )

        transition = await env.dispatch(
            movement_action(),
            command=CommandDispatchContext(
                command_id=token.command_id,
                based_on_revision=token.validated_revision,
            ),
            token=token,
        )

        # Even though every typed condition still holds, the lease-time scale
        # change means the coordinates are no longer meaningful.
        assert controller.actions == []
        boundary = transition.receipt.input_boundary
        assert boundary is not None
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "alibration" in boundary.reason

    asyncio.run(scenario())
