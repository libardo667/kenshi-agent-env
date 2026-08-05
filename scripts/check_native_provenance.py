#!/usr/bin/env python3
"""Report whether the installed native artefact matches its source and protocol."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kenshi_agent.tooling.native_provenance import (
    assess_native_provenance,
    render_native_provenance,
)

DEFAULT_BUILT = Path(
    "/mnt/c/Users/levib/AppData/Local/KenshiAgent/build/native/bin/KenshiAgentTelemetry.dll"
)
DEFAULT_INSTALLED = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Kenshi/mods/"
    "KenshiAgentTelemetry/KenshiAgentTelemetry.dll"
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--built", type=Path, default=DEFAULT_BUILT)
    parser.add_argument("--installed", type=Path, default=DEFAULT_INSTALLED)
    args = parser.parse_args()

    provenance = assess_native_provenance(built=args.built, installed=args.installed)
    print("\n".join(render_native_provenance(provenance)))
    sys.exit(0 if provenance.consistent else 1)
