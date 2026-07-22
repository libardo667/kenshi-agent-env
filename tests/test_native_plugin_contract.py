from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "native" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.cpp"


def test_native_plugin_does_not_export_unvalidated_raw_getting_eaten_byte() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert "character->isGettingEaten" not in source
    assert "getting-eaten state" in source
