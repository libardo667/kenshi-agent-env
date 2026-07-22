from __future__ import annotations

import base64
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOD_STUB = REPO_ROOT / "native" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.mod.base64"
STAGE_SCRIPT = REPO_ROOT / "scripts" / "stage_native.ps1"


def test_native_mod_stub_matches_upstream_examples() -> None:
    encoded = MOD_STUB.read_text(encoding="ascii").strip()
    decoded = base64.b64decode(encoded, validate=True)

    assert len(decoded) == 46
    assert hashlib.sha256(decoded).hexdigest() == (
        "ebdab65d330e46e1ff9725ac5d0ed87fd8c718cfb41ef85b27b86eb3d35b79c0"
    )


def test_staging_script_writes_the_validated_mod_stub() -> None:
    script = STAGE_SCRIPT.read_text(encoding="utf-8")

    assert "KenshiAgentTelemetry.mod.base64" in script
    assert "FromBase64String" in script
