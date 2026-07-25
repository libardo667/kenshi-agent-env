"""Report what the agent can know from a telemetry snapshot, and what it costs.

Run against live telemetry, or against a snapshot captured in a session log:

    python -m scripts.audit_fact_coverage --telemetry <telemetry.latest.json>
    python -m scripts.audit_fact_coverage --log runs/<run>/events.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kenshi_agent.fact_coverage import audit
from kenshi_agent.models import TelemetrySnapshot


def _from_log(path: Path) -> TelemetrySnapshot:
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("event_type") != "observation":
            continue
        payload = record.get("payload") or {}
        telemetry = payload.get("telemetry")
        if isinstance(telemetry, dict) and not payload.get("digest"):
            latest = telemetry
    if latest is None:
        raise SystemExit(
            f"{path} has no full observation to audit. Re-record with "
            "runtime.log_full_observations: true."
        )
    return TelemetrySnapshot.model_validate(latest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--telemetry", type=Path)
    source.add_argument("--log", type=Path)
    args = parser.parse_args()

    if args.telemetry is not None:
        snapshot = TelemetrySnapshot.model_validate_json(
            args.telemetry.read_text(encoding="utf-8")
        )
    else:
        snapshot = _from_log(args.log)

    report = audit(snapshot)
    print("\n".join(report.as_lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
