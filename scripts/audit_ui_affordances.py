"""Print what the agent can and cannot operate in Kenshi's interface.

Unlike `audit_fact_coverage`, this needs no running game: it compares Kenshi's
own GUI surface against our action catalog, so it answers "what should we build
next?" offline.
"""

from __future__ import annotations

from kenshi_agent.ui_affordances import audit


def main() -> int:
    report = audit()
    for line in report.as_lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
