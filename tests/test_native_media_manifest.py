from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "docs" / "native-media.lock.json"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def test_native_media_manifest_has_unique_verifiable_entries() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    entries = payload["media"]
    assert entries

    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))

    for entry in entries:
        assert entry["filename"].lower().endswith(".iso")
        assert entry["size_bytes"] > 0
        assert SHA256_PATTERN.fullmatch(entry["sha256"])
        assert entry["official_acquisition_url"].startswith("https://")
        assert entry["license_note"]
