"""Mechanical gates for current links, generated artifacts, and command references."""

from __future__ import annotations

import filecmp
import re
from pathlib import Path

from kenshi_agent.tooling.doc_export import export_docs
from kenshi_agent.tooling.schema_export import export_schemas

ROOT = Path(__file__).resolve().parents[1]
GENERATED_DOCS = ROOT / "docs" / "generated"
def test_relative_document_links_resolve() -> None:
    """A link to a deleted document is drift that reads as authority.

    The 2026-07-26 cleanup removed thirteen files; several surviving documents
    still pointed at them, which is exactly how a reader gets told that evidence
    exists somewhere it does not.
    """

    link = re.compile(r"\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)")
    broken: list[str] = []
    sources = sorted(ROOT.glob("docs/**/*.md"))
    sources += sorted(ROOT.glob("*.md"))
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


def _authored_docs() -> list[Path]:
    """Every hand-written document, which is every one a gate must police.

    `docs/generated/` is machine-written and already has its own staleness
    gates. `docs/REPORT_*.md` is write-once history: a report naming a command
    that has since been removed is an accurate record of when it was written,
    and rewriting it to please a gate would falsify the record.
    """

    docs = sorted(ROOT.glob("*.md"))
    docs += sorted(
        path
        for path in ROOT.glob("docs/*.md")
        if not path.name.startswith("REPORT_")
    )
    return docs


def _cli_subcommands() -> set[str]:
    """Ask the parser, so this cannot drift from the CLI it describes."""

    from kenshi_agent.application import build_parser

    names: set[str] = set()
    for action in build_parser()._subparsers._group_actions:  # noqa: SLF001
        names.update(action.choices)
    return names


def _dev_subcommands() -> set[str]:
    """Ask the shared dev parser, which is also what the wrapper asks."""

    from kenshi_agent.tooling.dev_cli import build_parser

    names: set[str] = set()
    for action in build_parser()._subparsers._group_actions:  # noqa: SLF001
        names.update(action.choices)
    return names


def test_authored_docs_only_name_commands_that_exist() -> None:
    """No authored document may send a reader to run something removed.

    Authored docs have named commands long after those commands left the CLI.
    Prose is the one place nobody diffs against the code, because it does not
    look like an interface - so the drift stays invisible until an agent burns
    a turn on it.

    Deliberately checks every authored doc rather than the one that was
    reported: fixing only the instance leaves the same defect in the files
    nobody happened to grep.
    """

    missing: list[str] = []
    for document in _authored_docs():
        text = document.read_text(encoding="utf-8")
        missing += [
            f"{document.relative_to(ROOT)}: {item}"
            for item in _missing_references(text, document.parent)
        ]

    assert not missing, (
        "authored documents name things that do not exist: "
        + ", ".join(missing)
        + ". Update the document, or restore what it describes."
    )


def test_generated_dev_reference_is_not_stale() -> None:
    from kenshi_agent.tooling.dev_cli import render_reference

    checked_in = ROOT / "docs" / "generated" / "DEV_CLI.md"
    assert checked_in.read_text(encoding="utf-8") == render_reference(), (
        "docs/generated/DEV_CLI.md is stale; run "
        "`python scripts/export_dev_cli.py`"
    )


def _code_spans(text: str) -> list[str]:
    """Every fenced block and every inline span, as separate strings.

    Fences are removed before inline spans are matched, because a naive
    `` `([^`]+)` `` pairs the first backtick of a ``` fence with the next
    backtick it finds and swallows the real inline spans after it. That is not
    hypothetical: it is why this gate reported green over a stale
    `kenshi-agent aggregate-affordances runs` sitting in README.md, and why the
    fenced copy of the same command in a guide went unchecked as well.
    """

    fenced = re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)
    inline = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
    return fenced + inline


def _missing_references(text: str, base: Path) -> list[str]:
    """Executable references that resolve to nothing.

    A document naming a sibling resolves against its own directory as well as
    the root, because `docs/ADR_X.md` legitimately writes `GUIDE_Y.md` for the
    file beside it.
    """

    def resolves(candidate: str) -> bool:
        return (base / candidate).exists() or (ROOT / candidate).exists()

    missing: list[str] = []

    spans = _code_spans(text)

    for span in spans:
        for name in re.findall(r"\bkenshi-agent ([a-z][a-z-]*)", span):
            if name not in _cli_subcommands():
                missing.append(f"kenshi-agent {name}")
        for name in re.findall(r"\./dev ([a-z][a-z-]*)", span):
            if name not in _dev_subcommands():
                missing.append(f"./dev {name}")

    # Paths are searched inside every backticked span rather than anchored to
    # its edges: executable references often include a leading interpreter,
    # and an edge-anchored pattern would silently check nothing there.
    for span in spans:
        for path in re.findall(r"\b((?:scripts|docs|config)/[\w./-]+)", span):
            # `docs/ADR_` and friends name a family, not a file.
            if not path.endswith((".py", ".md", ".yaml", ".json")):
                continue
            if not resolves(path.rstrip(".")):
                missing.append(path)

    # Deliberately not checking bare `SOME_DOC.md` cross-references. Those are
    # link rot, a real but separate problem, and the obvious check flags the
    # label of a correct markdown link like [`X.md`](docs/X.md). This gate is
    # about references that send a reader to run something that is gone.

    return missing
