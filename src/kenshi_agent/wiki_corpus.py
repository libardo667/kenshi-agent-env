"""Deterministic, attributed snapshots of the text-only Kenshi wiki corpus."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

FANDOM_API_URL = "https://kenshi.fandom.com/api.php"
FANDOM_WIKI_URL = "https://kenshi.fandom.com"
CC_BY_SA_3_URL = "https://creativecommons.org/licenses/by-sa/3.0/"

WikiQuery = Callable[[Mapping[str, str]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class WikiArticle:
    page_id: int
    namespace: int
    title: str
    revision_id: int
    revision_timestamp: str
    url: str
    text: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> WikiArticle:
        article = cls(
            page_id=int(record["page_id"]),
            namespace=int(record["namespace"]),
            title=str(record["title"]),
            revision_id=int(record["revision_id"]),
            revision_timestamp=str(record["revision_timestamp"]),
            url=str(record["url"]),
            text=str(record["text"]),
        )
        if article.page_id <= 0 or article.revision_id <= 0:
            raise ValueError("wiki page and revision IDs must be positive")
        if not article.title or not article.url or not article.text.strip():
            raise ValueError("wiki articles require title, URL, and text")
        return article

    def record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WikiSearchResult:
    article: WikiArticle
    score: int
    snippet: str


def _articles_from_query(payload: Mapping[str, Any]) -> list[WikiArticle]:
    query = payload.get("query")
    if not isinstance(query, Mapping):
        raise ValueError("wiki response has no query object")  # pragma: no mutate
    pages = query.get("pages")
    if not isinstance(pages, list):
        raise ValueError("wiki response has no pages list")  # pragma: no mutate

    articles: list[WikiArticle] = []
    for page in pages:
        if not isinstance(page, Mapping):
            raise ValueError("wiki page is not an object")  # pragma: no mutate
        revisions = page.get("revisions")
        if not isinstance(revisions, list):
            raise ValueError(  # pragma: no mutate
                f"wiki page {page.get('title')!r} has no revisions list"
            )
        if len(revisions) != 1:
            raise ValueError(  # pragma: no mutate
                f"wiki page {page.get('title')!r} has no exact revision"
            )
        revision = revisions[0]
        if not isinstance(revision, Mapping):
            raise ValueError("wiki revision is not an object")  # pragma: no mutate
        slots = revision.get("slots")
        if not isinstance(slots, Mapping):
            raise ValueError("wiki revision has no slots")  # pragma: no mutate
        main = slots.get("main")
        if not isinstance(main, Mapping):
            raise ValueError("wiki revision has no main slot")  # pragma: no mutate
        articles.append(
            WikiArticle.from_record(
                {
                    "page_id": page["pageid"],
                    "namespace": page["ns"],
                    "title": page["title"],
                    "revision_id": revision["revid"],
                    "revision_timestamp": revision["timestamp"],
                    "url": page["canonicalurl"],
                    "text": main["content"],
                }
            )
        )
    return articles


def fetch_main_namespace(query: WikiQuery) -> list[WikiArticle]:
    """Fetch every current main-namespace revision through MediaWiki paging."""

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
    continuation: dict[str, str] = {}
    by_page_id: dict[int, WikiArticle] = {}
    while True:
        payload = query({**base, **continuation})
        for article in _articles_from_query(payload):
            if article.namespace != 0:
                raise ValueError(  # pragma: no mutate
                    f"wiki page {article.title!r} escaped the main namespace"
                )
            previous = by_page_id.setdefault(article.page_id, article)
            if previous != article:
                raise ValueError(f"wiki page ID {article.page_id} changed during sync")

        next_page = payload.get("continue")
        if next_page is None:
            break
        if not isinstance(next_page, Mapping):
            raise ValueError("wiki continuation is not an object")  # pragma: no mutate
        continuation = {str(key): str(value) for key, value in next_page.items()}

    return sorted(by_page_id.values(), key=lambda article: article.title.casefold())


def write_snapshot(path: Path, articles: Iterable[WikiArticle]) -> None:
    ordered = sorted(articles, key=lambda article: article.title.casefold())
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 spelling and newline transport aliases have no corpus semantics.
    with path.open(  # pragma: no mutate
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for article in ordered:
            # JSON whitespace, key order, and codec spelling are one internal
            # canonicalization choice; round-trip and digest are the contract.
            # pragma: no mutate start
            handle.write(
                json.dumps(
                    article.record(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
            # pragma: no mutate end


def load_snapshot(path: Path) -> list[WikiArticle]:
    articles: list[WikiArticle] = []
    with path.open(encoding="utf-8") as handle:  # pragma: no mutate
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(  # pragma: no mutate
                    f"blank wiki snapshot row at line {line_number}"
                )
            articles.append(WikiArticle.from_record(json.loads(line)))
    return articles


def snapshot_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


# Snippet windowing is bounded human presentation, not corpus search semantics.
# pragma: no mutate start
def _search_snippet(article: WikiArticle, terms: tuple[str, ...]) -> str:
    body = article.text.casefold()
    first = min(
        (body.find(term) for term in terms if body.find(term) >= 0),
        default=0,
    )
    start = max(0, first - 120)
    end = min(len(article.text), first + 360)
    return " ".join(article.text[start:end].split())


# pragma: no mutate end


def search_articles(
    articles: Iterable[WikiArticle],
    query: str,
    *,
    limit: int = 10,
) -> list[WikiSearchResult]:
    """Return a bounded literal full-text search over the checked-in corpus."""

    terms = tuple(term for term in query.casefold().split() if term)
    if not terms:
        return []
    if limit < 0:
        raise ValueError(  # pragma: no mutate
            "wiki search limit cannot be negative"
        )
    ranked: list[tuple[int, WikiSearchResult]] = []
    for article in articles:
        title = article.title.casefold()
        body = article.text.casefold()
        haystack = f"{title}\n{body}"
        if not all(term in haystack for term in terms):
            continue
        score = sum(body.count(term) for term in terms)
        title_hits = sum(term in title for term in terms)
        ranked.append(
            (
                title_hits,
                WikiSearchResult(article, score, _search_snippet(article, terms)),
            )
        )
    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1].score,
            item[1].article.title.casefold(),
        )
    )
    return [item[1] for item in ranked[:limit]]
