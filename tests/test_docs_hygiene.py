"""Mechanical gates against documentation drift and sprawl.

Prose asking agents to be concise loses to the next loop's instincts. These
checks do not ask. Every document must be one of three shapes, must stay under a
hard line cap, and must fail the build rather than quietly lie when the thing it
describes has moved.

A document is legitimate only if it is:

* `docs/ADR_*.md` — a durable decision, written once, superseded not edited;
* `docs/GUIDE_*.md` — a procedure or wire contract an implementer needs, which
  restates no code and has no other home;
* `docs/generated/*` — emitted from the code that is its source of truth.

Anything else is either restating code (generate it) or restating history (the
commit log and `runs/` already hold it). `LEGACY_DOCS` is deliberately empty: the
2026-07-26 cleanup deleted or converted all 25 grandfathered files. Re-adding an
entry is admitting a new exception, so do it loudly or not at all.
"""

from __future__ import annotations

import filecmp
import re
from pathlib import Path

import pytest

from kenshi_agent.doc_export import export_docs
from kenshi_agent.schema_export import export_schemas

ROOT = Path(__file__).resolve().parents[1]
DOC_LINE_CAP = 120
GENERATED_DOCS = ROOT / "docs" / "generated"

# Root-level documents that are not `docs/` shapes but still must stay short.
# `CHANGELOG.md` is absent on purpose: an append-only history is meant to grow.
ROOT_DOCS = (
    "README.md",
    "ARCHITECTURE.md",
    "STATUS.md",
    "SECURITY_AND_SAFETY.md",
)

LEGACY_DOCS: dict[str, int] = {}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _capped_docs() -> list[Path]:
    paths = sorted(ROOT.glob("docs/**/*.md"))
    paths += [ROOT / name for name in ROOT_DOCS]
    return [path for path in paths if path.is_file()]


@pytest.mark.parametrize("relative", sorted(LEGACY_DOCS) or ["<none>"])
def test_legacy_docs_may_only_shrink(relative: str) -> None:
    """Frozen debt, if any is ever re-admitted. Lower the number; never raise it."""

    if relative == "<none>":
        pytest.skip("no grandfathered documents remain, which is the goal")
    path = ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} was deleted, which is the point of the ratchet")
    ceiling = LEGACY_DOCS[relative]
    actual = _line_count(path)
    assert actual <= ceiling, f"{relative} grew from {ceiling} to {actual} lines"


def test_documents_stay_under_the_line_cap() -> None:
    oversized = {
        path.relative_to(ROOT).as_posix(): _line_count(path)
        for path in _capped_docs()
        if path.relative_to(ROOT).as_posix() not in LEGACY_DOCS
        and _line_count(path) > DOC_LINE_CAP
    }
    assert not oversized, (
        f"documentation over {DOC_LINE_CAP} lines: {oversized}. Split it, cut it, "
        "or move the narrative into the commit message that carries the evidence."
    )


def test_docs_are_decision_records_guides_or_generated() -> None:
    """A new doc has exactly three legitimate shapes; everything else is sprawl."""

    stray = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.glob("docs/*.md")
        if not path.name.startswith(("ADR_", "GUIDE_"))
        and path.relative_to(ROOT).as_posix() not in LEGACY_DOCS
    )
    assert not stray, (
        f"unclassified docs: {stray}. A doc must be a durable decision record "
        "(docs/ADR_*.md), a procedure or wire contract (docs/GUIDE_*.md), or "
        "generated output (docs/generated/), or it will drift."
    )


def test_relative_document_links_resolve() -> None:
    """A link to a deleted document is drift that reads as authority.

    The 2026-07-26 cleanup removed thirteen files; several surviving documents
    still pointed at them, which is exactly how a reader gets told that evidence
    exists somewhere it does not.
    """

    link = re.compile(r"\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)")
    broken: list[str] = []
    sources = sorted(ROOT.glob("docs/**/*.md"))
    sources += [ROOT / name for name in (*ROOT_DOCS, "LOOP_PROMPT.md")]
    sources.append(ROOT / "native" / "KenshiAgentTelemetry" / "README.md")
    for source in sources:
        if not source.is_file():
            continue
        for target in link.findall(source.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (source.parent / target).exists():
                broken.append(f"{source.relative_to(ROOT).as_posix()} -> {target}")
    assert not broken, f"links to missing files: {broken}"


def test_generated_docs_are_not_stale(tmp_path: Path) -> None:
    for fresh in export_docs(tmp_path):
        checked_in = GENERATED_DOCS / fresh.name
        assert checked_in.exists(), (
            f"{fresh.name} is generated but not checked in; "
            "run `python scripts/export_docs.py`"
        )
        assert filecmp.cmp(fresh, checked_in, shallow=False), (
            f"docs/generated/{fresh.name} is stale; "
            "run `python scripts/export_docs.py`"
        )


def test_exported_schemas_are_not_stale(tmp_path: Path) -> None:
    """The changelog regenerated these by hand once. Now the suite checks."""

    for fresh in export_schemas(tmp_path):
        checked_in = ROOT / "schemas" / fresh.name
        assert checked_in.exists(), (
            f"{fresh.name} is generated but not checked in; "
            "run `python scripts/export_schemas.py`"
        )
        assert filecmp.cmp(fresh, checked_in, shallow=False), (
            f"schemas/{fresh.name} is stale; run `python scripts/export_schemas.py`"
        )
