from __future__ import annotations

from kenshi_agent.blocker_ledger import parse_ledger


def test_parsing_collapses_legacy_trailing_space_signatures() -> None:
    blockers, newest_run_id = parse_ledger(
        """
newest run  run-new
| `same failure ` | old example | 1 | 1 | run-old | 2026-07-30T01:00:00Z |
| `same failure` | new example | 1 | 1 | run-new | 2026-07-30T02:00:00Z |
"""
    )

    assert newest_run_id == "run-new"
    assert set(blockers) == {"same failure"}
    assert blockers["same failure"].last_run_id == "run-new"
