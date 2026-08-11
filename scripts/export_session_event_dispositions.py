#!/usr/bin/env python3
"""Export the source-derived, human-reviewed session event disposition map."""

from kenshi_agent.tooling.session_event_dispositions import (
    export_generated_dispositions,
)


def main() -> None:
    print(export_generated_dispositions())


if __name__ == "__main__":
    main()
