#!/usr/bin/env python3
"""Search the checked-in text-only Kenshi Wiki snapshot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kenshi_agent.wiki_corpus import load_snapshot, search_articles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "knowledge" / "wiki" / "kenshi_fandom_main_namespace.jsonl",
    )
    args = parser.parse_args()

    results = search_articles(
        load_snapshot(args.snapshot),
        args.query,
        limit=args.limit,
    )
    for result in results:
        article = result.article
        print(f"{article.title} | score={result.score} | rev={article.revision_id}")
        print(article.url)
        print(result.snippet)
        print()
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

