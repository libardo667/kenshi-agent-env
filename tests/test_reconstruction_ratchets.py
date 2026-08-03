from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reconstruction_demolition_targets_do_not_grow() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_reconstruction_ratchets.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
