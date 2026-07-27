from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import load_config
from kenshi_agent.graphics_profile import (
    GraphicsProfile,
    apply_graphics_profile,
    load_graphics_profile,
    verify_graphics_profile,
)


def profile(**settings: str) -> GraphicsProfile:
    return GraphicsProfile(
        profile_id="test-profile",
        settings=settings,
    )


def test_live_profiles_share_a_strictly_lower_workload_than_v2() -> None:
    root = Path(__file__).resolve().parents[1]
    baseline = load_graphics_profile(
        root / "config" / "graphics" / "iris-xe-stability-v2.yaml"
    )
    live_configs = (
        root / "config" / "live.burnin.yaml",
        root / "config" / "live.dialogue.yaml",
        root / "config" / "live.longform.yaml",
    )
    loaded_configs = tuple(load_config(config_path) for config_path in live_configs)
    active_paths = {
        config.launch.graphics_profile_file
        for config in loaded_configs
    }
    assert None not in active_paths
    assert len(active_paths) == 1
    assert all(
        (config.controls.calibrated_client_width, config.controls.calibrated_client_height)
        == (1920, 1080)
        for config in loaded_configs
    )
    active_path = active_paths.pop()
    assert active_path is not None
    active = load_graphics_profile(active_path)

    lower_is_cheaper = {
        "terrain hi-res distance",
        "view distance",
        "water reflection",
        "shadow mode",
        "terrain detail",
        "terrain distant",
        "grass range",
        "grass density",
        "foliage range",
        "npc range",
        "objects view range",
        "feature range",
        "distant town range",
        "generate distant towns",
        "reflection range",
        "shadow quality",
        "Shadow Range",
        "Decal Resolution",
        "Decal Range",
        "FXAA",
        "HeatHaze",
    }
    higher_is_cheaper = {"texture resolution gimping"}
    assert active.settings.keys() == baseline.settings.keys()
    workload_deltas = {
        key: (
            float(active.settings[key]),
            float(baseline.settings[key]),
        )
        for key in lower_is_cheaper
    }

    assert all(
        active_value <= baseline_value
        for active_value, baseline_value in workload_deltas.values()
    )
    assert any(
        active_value < baseline_value
        for active_value, baseline_value in workload_deltas.values()
    )
    assert all(
        float(active.settings[key]) >= float(baseline.settings[key])
        for key in higher_is_cheaper
    )
    unchanged = active.settings.keys() - lower_is_cheaper - higher_is_cheaper
    assert all(
        active.settings[key] == baseline.settings[key]
        for key in unchanged
    )


def test_verify_reports_missing_and_different_settings_case_insensitively(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.cfg"
    settings.write_text(
        "View Distance=2500\nwater reflection=0\nunrelated=yes\n",
        encoding="utf-8",
    )

    result = verify_graphics_profile(
        settings,
        profile(**{"view distance": "1500", "Water Reflection": "0", "FXAA": "0"}),
    )

    assert not result.matches
    assert [
        (mismatch.key, mismatch.expected, mismatch.actual)
        for mismatch in result.mismatches
    ] == [
        ("view distance", "1500", "2500"),
        ("FXAA", "0", None),
    ]


def test_apply_is_atomic_preserves_unknowns_and_creates_recoverable_backup(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.cfg"
    original = (
        "language=en_GB\r\n"
        "view distance=2500\r\n"
        "water reflection=2\r\n"
        "# retain this comment\r\n"
    )
    settings.write_text(original, encoding="utf-8", newline="")
    expected = profile(
        **{
            "view distance": "1500",
            "water reflection": "0",
            "FXAA": "0",
        }
    )

    result = apply_graphics_profile(
        settings,
        expected,
        now=datetime(2026, 7, 23, 23, 59, tzinfo=UTC),
    )

    assert result.changed
    assert result.verification.matches
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    with result.backup_path.open("r", encoding="utf-8", newline="") as handle:
        assert handle.read() == original
    with settings.open("r", encoding="utf-8", newline="") as handle:
        installed = handle.read()
    assert "language=en_GB\r\n" in installed
    assert "view distance=1500\r\n" in installed
    assert "water reflection=0\r\n" in installed
    assert "# retain this comment\r\n" in installed
    assert installed.endswith("FXAA=0\r\n")


def test_apply_exact_profile_is_idempotent_and_makes_no_backup(tmp_path: Path) -> None:
    settings = tmp_path / "settings.cfg"
    settings.write_text("view distance=1500\nFXAA=0\n", encoding="utf-8")

    result = apply_graphics_profile(
        settings,
        profile(**{"view distance": "1500", "FXAA": "0"}),
    )

    assert not result.changed
    assert result.backup_path is None
    assert list(tmp_path.iterdir()) == [settings]


def test_malformed_or_duplicate_settings_fail_closed(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.cfg"
    malformed.write_text("not a setting\n", encoding="utf-8")
    duplicate = tmp_path / "duplicate.cfg"
    duplicate.write_text("FXAA=1\nfxaa=0\n", encoding="utf-8")
    expected = profile(FXAA="0")

    with pytest.raises(ValueError, match="expected key=value"):
        verify_graphics_profile(malformed, expected)
    with pytest.raises(ValueError, match="duplicate keys"):
        verify_graphics_profile(duplicate, expected)
