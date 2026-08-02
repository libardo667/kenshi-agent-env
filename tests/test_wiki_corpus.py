from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kenshi_agent.wiki_corpus import (
    CC_BY_SA_3_URL,
    WikiArticle,
    fetch_main_namespace,
    load_snapshot,
    search_articles,
    snapshot_digest,
    write_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "knowledge" / "wiki" / "kenshi_fandom_main_namespace.jsonl"
MANIFEST = ROOT / "knowledge" / "wiki" / "manifest.json"


def _page(page_id: int, title: str, text: str) -> dict[str, Any]:
    return {
        "pageid": page_id,
        "ns": 0,
        "title": title,
        "canonicalurl": f"https://kenshi.fandom.com/wiki/{title}",
        "revisions": [
            {
                "revid": page_id + 100,
                "timestamp": "2026-08-02T00:00:00Z",
                "slots": {"main": {"content": text}},
            }
        ],
    }


def test_fetch_main_namespace_follows_exact_continuation_and_sorts() -> None:
    calls: list[dict[str, str]] = []

    def query(parameters: dict[str, str]) -> dict[str, Any]:
        calls.append(parameters)
        if len(calls) == 1:
            return {
                "continue": {"gapcontinue": "Alpha", "continue": "gapcontinue||"},
                "query": {"pages": [_page(2, "Zulu", "last")]},
            }
        return {"query": {"pages": [_page(1, "Alpha", "first")]}}

    articles = fetch_main_namespace(query)

    assert [article.title for article in articles] == ["Alpha", "Zulu"]
    base = {
        "action": "query",
        "generator": "allpages",
        "gapnamespace": "0",
        "gaplimit": "50",
        "prop": "revisions|info",
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "inprop": "url",
        "format": "json",
        "formatversion": "2",
    }
    assert calls == [
        base,
        {**base, "gapcontinue": "Alpha", "continue": "gapcontinue||"},
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "no query object"),
        ({"query": {}}, "no pages list"),
        ({"query": {"pages": [None]}}, "page is not an object"),
        (
            {"query": {"pages": [{"title": "Bad", "revisions": "x"}]}},
            "no revisions list",
        ),
        (
            {"query": {"pages": [{"title": "Bad", "revisions": []}]}},
            "no exact revision",
        ),
        (
            {"query": {"pages": [{"title": "Bad", "revisions": [None]}]}},
            "revision is not an object",
        ),
        (
            {
                "query": {
                    "pages": [
                        {"title": "Bad", "revisions": [{"slots": None}]}
                    ]
                }
            },
            "revision has no slots",
        ),
        (
            {
                "query": {
                    "pages": [
                        {"title": "Bad", "revisions": [{"slots": {}}]}
                    ]
                }
            },
            "revision has no main slot",
        ),
    ],
)
def test_fetch_fails_closed_on_malformed_api_shapes(
    payload: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        fetch_main_namespace(lambda parameters: payload)


def test_fetch_rejects_invalid_continuation_and_non_main_namespace() -> None:
    with pytest.raises(ValueError, match="continuation is not an object"):
        fetch_main_namespace(
            lambda parameters: {
                "continue": "next",
                "query": {"pages": [_page(1, "Alpha", "first")]},
            }
        )

    escaped = _page(1, "Talk", "not main")
    escaped["ns"] = 1
    with pytest.raises(ValueError, match="escaped the main namespace"):
        fetch_main_namespace(
            lambda parameters: {"query": {"pages": [escaped]}}
        )


def test_fetch_fails_if_one_page_changes_during_the_snapshot() -> None:
    calls = 0

    def query(parameters: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        del parameters
        calls += 1
        page = _page(1, "Same", "first" if calls == 1 else "changed")
        if calls == 1:
            return {
                "continue": {"gapcontinue": "Same", "continue": "gapcontinue||"},
                "query": {"pages": [page]},
            }
        return {"query": {"pages": [page]}}

    with pytest.raises(ValueError, match="changed during sync"):
        fetch_main_namespace(query)


def test_snapshot_round_trip_is_stable_and_sorted(tmp_path: Path) -> None:
    snapshot = tmp_path / "nested" / "deeper" / "wiki.jsonl"
    articles = [
        WikiArticle(2, 0, "Zulu", 12, "2026-08-02T00:00:00Z", "https://x/Z", "zé"),
        WikiArticle(1, 0, "Alpha", 11, "2026-08-02T00:00:00Z", "https://x/A", "a"),
    ]

    write_snapshot(snapshot, articles)
    first_digest = snapshot_digest(snapshot)

    assert load_snapshot(snapshot) == list(reversed(articles))
    raw = snapshot.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert "zé" in raw
    assert "\\u00e9" not in raw
    assert ": " not in raw
    assert all(
        list(json.loads(line)) == sorted(json.loads(line))
        for line in raw.splitlines()
    )
    write_snapshot(snapshot, load_snapshot(snapshot))
    assert snapshot_digest(snapshot) == first_digest


def test_snapshot_reports_the_exact_blank_row(tmp_path: Path) -> None:
    snapshot = tmp_path / "wiki.jsonl"
    snapshot.write_text(
        json.dumps(WikiArticle(1, 0, "A", 2, "t", "u", "a").record())
        + "\n\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blank wiki snapshot row at line 2"):
        load_snapshot(snapshot)


def test_search_requires_every_term_and_prefers_title_matches() -> None:
    articles = [
        WikiArticle(
            3,
            0,
            "Unrelated",
            13,
            "2026-08-02T00:00:00Z",
            "https://x/U",
            "Shift exists without the other terms.",
        ),
        WikiArticle(
            1,
            0,
            "Jobs",
            11,
            "2026-08-02T00:00:00Z",
            "https://x/J",
            "Shift right-click adds a persistent task.",
        ),
        WikiArticle(
            2,
            0,
            "Controls",
            12,
            "2026-08-02T00:00:00Z",
            "https://x/C",
            "The Jobs guide says Shift right-click.",
        ),
    ]

    results = search_articles(articles, "jobs shift right-click", limit=2)

    assert [result.article.title for result in results] == ["Jobs", "Controls"]
    assert [result.score for result in results] == [2, 3]
    assert "Shift right-click" in results[0].snippet
    assert search_articles(articles, "", limit=2) == []
    assert search_articles(articles, "shift", limit=0) == []
    assert len(search_articles(articles, "shift", limit=1)) == 1
    with pytest.raises(ValueError, match="cannot be negative"):
        search_articles(articles, "shift", limit=-1)


def test_search_default_limit_and_ties_are_deterministic() -> None:
    many = [
        WikiArticle(
            page_id=index,
            namespace=0,
            title=f"Page {index:02d}",
            revision_id=100 + index,
            revision_timestamp="2026-08-02T00:00:00Z",
            url=f"https://x/{index}",
            text="same match",
        )
        for index in range(1, 13)
    ]

    results = search_articles(reversed(many), "same match")

    assert len(results) == 10
    assert [result.article.title for result in results] == [
        f"Page {index:02d}" for index in range(1, 11)
    ]
    assert all(result.score == 2 for result in results)

    relevance = search_articles(
        [
            WikiArticle(20, 0, "Alpha", 120, "t", "u", "same match twice match"),
            WikiArticle(21, 0, "Beta", 121, "t", "u", "same match"),
        ],
        "same match",
    )
    assert [result.article.title for result in relevance] == ["Alpha", "Beta"]
    assert [result.score for result in relevance] == [3, 2]


def test_checked_in_wiki_snapshot_is_complete_attributed_and_content_addressed() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    articles = load_snapshot(SNAPSHOT)

    assert manifest["namespace"] == 0
    assert manifest["article_count"] == len(articles)
    assert len(articles) >= 2_000
    assert manifest["snapshot_sha256"] == snapshot_digest(SNAPSHOT)
    assert manifest["license_url"] == CC_BY_SA_3_URL
    assert all(article.namespace == 0 for article in articles)
    assert all(article.revision_id > 0 for article in articles)
    assert all(article.url.startswith("https://kenshi.fandom.com/wiki/") for article in articles)
    assert all(article.text.strip() for article in articles)
    assert len({article.page_id for article in articles}) == len(articles)
    assert [article.title.casefold() for article in articles] == sorted(
        article.title.casefold() for article in articles
    )


def test_snapshot_contains_the_mechanic_pages_used_by_the_reviewed_fact_index() -> None:
    by_title = {article.title: article for article in load_snapshot(SNAPSHOT)}

    for title in (
        "Controls",
        "Food",
        "Guide to Health",
        "Guide to Training Statistics",
        "Jobs",
        "Strength",
    ):
        assert title in by_title
        assert len(by_title[title].text) > 100
