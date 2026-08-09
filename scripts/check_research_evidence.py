#!/usr/bin/env python3
"""Validate every canonical reverse-engineering evidence package."""

from kenshi_agent.tooling.research_evidence import validate_research_tree

if __name__ == "__main__":
    errors = validate_research_tree()
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Reverse-engineering evidence packages pass.")
