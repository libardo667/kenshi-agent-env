from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import load_config
from kenshi_agent.graphics_profile import (
    GraphicsProfile,
    RendererProfile,
    apply_graphics_profile,
    load_graphics_profile,
    verify_graphics_profile,
)


def profile(**settings: str) -> GraphicsProfile:
    return GraphicsProfile(
        profile_id="test-profile",
        settings=settings,
    )


def test_canonical_live_config_uses_a_strictly_lower_workload_than_v2() -> None:
    root = Path(__file__).resolve().parents[1]
    baseline = load_graphics_profile(
        root / "config" / "graphics" / "iris-xe-stability-v2.yaml"
    )
    loaded_configs = (load_config(root / "config" / "live.yaml"),)
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
    assert active.renderer == RendererProfile(
        section="Direct3D11 Rendering Subsystem",
        settings={
            "VSync": "Yes",
            "VSync Interval": "2",
            "Video Mode": "1920 x 1080 @ 32-bit colour [0]",
        },
    )

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


def test_apply_updates_settings_and_renderer_as_one_recoverable_bundle(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.cfg"
    renderer = tmp_path / "kenshi.cfg"
    original_settings = "view distance=2500\r\nlanguage=en_GB\r\n"
    original_renderer = (
        "Render System=Direct3D11 Rendering Subsystem\r\n"
        "[Direct3D11 Rendering Subsystem]\r\n"
        "VSync=Yes\r\n"
        "VSync Interval=1\r\n"
        "Video Mode=1920 x 1080 @ 32-bit colour [0]\r\n"
        "[Unrelated]\r\n"
        "VSync Interval=9\r\n"
    )
    settings.write_text(original_settings, encoding="utf-8", newline="")
    renderer.write_text(original_renderer, encoding="utf-8", newline="")
    expected = GraphicsProfile(
        profile_id="test-profile",
        settings={"view distance": "1000"},
        renderer=RendererProfile(
            section="Direct3D11 Rendering Subsystem",
            settings={"VSync": "Yes", "VSync Interval": "2"},
        ),
    )

    result = apply_graphics_profile(
        settings,
        expected,
        renderer_path=renderer,
        now=datetime(2026, 7, 26, 23, 59, tzinfo=UTC),
    )

    assert result.changed
    assert result.verification.matches
    assert len(result.backup_paths) == 2
    assert {
        path.read_text(encoding="utf-8")
        for path in result.backup_paths
    } == {
        original_settings.replace("\r\n", "\n"),
        original_renderer.replace("\r\n", "\n"),
    }
    with settings.open("r", encoding="utf-8", newline="") as handle:
        installed_settings = handle.read()
    with renderer.open("r", encoding="utf-8", newline="") as handle:
        installed_renderer = handle.read()
    assert installed_settings == "view distance=1000\r\nlanguage=en_GB\r\n"
    assert "VSync Interval=2\r\n" in installed_renderer
    assert "[Unrelated]\r\nVSync Interval=9\r\n" in installed_renderer


def test_bundle_apply_restores_first_file_when_second_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings.cfg"
    renderer = tmp_path / "kenshi.cfg"
    original_settings = "view distance=2500\n"
    original_renderer = (
        "[Direct3D11 Rendering Subsystem]\n"
        "VSync Interval=1\n"
    )
    settings.write_text(original_settings, encoding="utf-8")
    renderer.write_text(original_renderer, encoding="utf-8")
    expected = GraphicsProfile(
        profile_id="test-profile",
        settings={"view distance": "1000"},
        renderer=RendererProfile(
            section="Direct3D11 Rendering Subsystem",
            settings={"VSync Interval": "2"},
        ),
    )
    real_replace = __import__("os").replace

    def fail_renderer_replace(source: Path | str, destination: Path | str) -> None:
        if Path(destination) == renderer:
            raise OSError("simulated renderer replace failure")
        real_replace(source, destination)

    monkeypatch.setattr("kenshi_agent.graphics_profile.os.replace", fail_renderer_replace)

    with pytest.raises(OSError, match="renderer replace failure"):
        apply_graphics_profile(settings, expected, renderer_path=renderer)

    assert settings.read_text(encoding="utf-8") == original_settings
    assert renderer.read_text(encoding="utf-8") == original_renderer


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
