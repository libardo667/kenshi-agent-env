from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def test_repository_stability_profile_is_strict_and_versioned() -> None:
    root = Path(__file__).resolve().parents[1]

    loaded = load_graphics_profile(
        root / "config" / "graphics" / "iris-xe-stability-v2.yaml"
    )

    assert loaded.format_version == 1
    assert loaded.profile_id == "iris-xe-stability-v2"
    assert loaded.settings["texture resolution gimping"] == "3"
    assert loaded.settings["view distance"] == "1500"
    assert loaded.settings["water reflection"] == "0"
    assert loaded.settings["FXAA"] == "0"
    assert loaded.settings["HeatHaze"] == "0"


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
