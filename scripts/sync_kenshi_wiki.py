#!/usr/bin/env python3
"""Refresh the attributed text-only Kenshi Fandom snapshot."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kenshi_agent.wiki_corpus import (  # noqa: E402
    CC_BY_SA_3_URL,
    FANDOM_API_URL,
    FANDOM_WIKI_URL,
    fetch_main_namespace,
    snapshot_digest,
    write_snapshot,
)

USER_AGENT = "kenshi-agent-env/1.0 (text-only research snapshot)"


def _query(parameters: dict[str, str]) -> dict[str, Any]:
    request = Request(
        f"{FANDOM_API_URL}?{urlencode(parameters)}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=60.0) as response:  # noqa: S310
                payload = json.loads(response.read())
            if not isinstance(payload, dict):
                raise ValueError("wiki API returned a non-object")
            if "error" in payload:
                raise RuntimeError(f"wiki API error: {payload['error']}")
            time.sleep(0.05)
            return payload
        except Exception as exc:  # network failures need bounded retries
            last_error = exc
            if attempt == 3:
                break
            time.sleep(2**attempt)
    raise RuntimeError("wiki API failed after four attempts") from last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "knowledge" / "wiki",
    )
    args = parser.parse_args()

    articles = fetch_main_namespace(_query)
    snapshot = args.output_dir / "kenshi_fandom_main_namespace.jsonl"
    write_snapshot(snapshot, articles)
    manifest = {
        "schema_version": 1,
        "source_wiki": FANDOM_WIKI_URL,
        "source_api": FANDOM_API_URL,
        "namespace": 0,
        "content_format": "MediaWiki wikitext",
        "article_count": len(articles),
        "snapshot_sha256": snapshot_digest(snapshot),
        "snapshot_generated_at": datetime.now(UTC).isoformat(),
        "latest_revision_timestamp": max(
            article.revision_timestamp for article in articles
        ),
        "license": "CC-BY-SA-3.0",
        "license_url": CC_BY_SA_3_URL,
        "attribution": (
            "Kenshi Wiki contributors; each row preserves its canonical article "
            "URL and exact revision ID."
        ),
        "excluded": [
            "non-main namespaces",
            "images and other binary media",
            "rendered site chrome",
            "revision history before the captured revision",
        ],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(articles)} articles -> {snapshot}")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

