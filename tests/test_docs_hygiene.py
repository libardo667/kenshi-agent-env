"""Mechanical gates for current links, generated artifacts, and command references."""

from __future__ import annotations

import filecmp
import re
from pathlib import Path

import pytest

from kenshi_agent.blocker_ledger import LEDGER_NAME as BLOCKER_LEDGER_NAME
from kenshi_agent.blocker_ledger import (
    export_blocker_ledger,
    newest_run_bundles,
)
from kenshi_agent.doc_export import export_docs
from kenshi_agent.mutation_ledger import (
    LEDGER_NAME,
    export_mutation_ledger,
    sources_are_instrumented,
)
from kenshi_agent.schema_export import export_schemas

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


def test_the_mutation_ledger_is_not_stale(tmp_path: Path) -> None:
    """Editing a mutated module must invalidate the claim that it was mutated.

    Regeneration reads only committed inputs — the checked-in ledger and the
    sources it digests — so this fails on a clone with no `runs/` artifacts at
    all, which is the point: the gate cannot depend on the machine that ran the
    campaign. `scripts/export_mutation_ledger.py` additionally folds in local
    artifacts, which is how new evidence enters the record.

    The two gates above compare *behavior*, so they hold under mutation. This one
    compares bytes; see `sources_are_instrumented` for why that cannot.
    """

    if sources_are_instrumented(ROOT):
        pytest.skip("mutmut instruments the sources this gate digests")

    fresh = export_mutation_ledger(
        tmp_path,
        repo_root=ROOT,
        existing=GENERATED_DOCS / LEDGER_NAME,
    )
    checked_in = GENERATED_DOCS / LEDGER_NAME
    assert checked_in.exists(), (
        f"{LEDGER_NAME} is generated but not checked in; "
        "run `python scripts/export_mutation_ledger.py`"
    )
    assert filecmp.cmp(fresh, checked_in, shallow=False), (
        f"docs/generated/{LEDGER_NAME} is stale — a module moved since its "
        "mutation campaign, or new evidence has not been folded in. Run "
        "`python scripts/export_mutation_ledger.py`."
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


def test_the_blocker_ledger_round_trips_through_its_own_format(
    tmp_path: Path,
) -> None:
    """Rendering and parsing must be exact inverses.

    Deliberately weak, and named so nobody mistakes it for more: with no
    `runs/` the export parses the committed file and re-renders it, so a
    hand-edited count survives untouched. What it does catch is a format that
    cannot be read back, which would silently drop every recorded row the next
    time anyone regenerated. The gate below is the one that checks content, and
    it needs evidence to do it.
    """

    fresh = export_blocker_ledger(
        tmp_path,
        existing=GENERATED_DOCS / BLOCKER_LEDGER_NAME,
    )
    checked_in = GENERATED_DOCS / BLOCKER_LEDGER_NAME
    assert checked_in.exists(), (
        f"{BLOCKER_LEDGER_NAME} is generated but not checked in; "
        "run `python scripts/export_blocker_ledger.py`"
    )
    assert filecmp.cmp(fresh, checked_in, shallow=False), (
        f"docs/generated/{BLOCKER_LEDGER_NAME} does not survive a round trip "
        "through its own parser. Run `python scripts/export_blocker_ledger.py`."
    )


def test_the_blocker_ledger_matches_the_local_run_evidence(tmp_path: Path) -> None:
    """On a machine holding bundles, the ledger must be what they derive.

    This is the forcing function. It re-reads the evidence and compares the
    whole document, so it fails both when runs have accumulated since the last
    export and when a row was edited by hand - neither of which a clone can
    detect, because the evidence is machine-local by design.

    Skips where there is nothing to be behind, the same way binding parity only
    compares against an installed game when one is present.
    """

    if not newest_run_bundles(ROOT / "runs", limit=1):
        pytest.skip("no local run bundles to derive from")

    fresh = export_blocker_ledger(
        tmp_path,
        runs_dir=ROOT / "runs",
        existing=GENERATED_DOCS / BLOCKER_LEDGER_NAME,
    )
    assert filecmp.cmp(
        fresh, GENERATED_DOCS / BLOCKER_LEDGER_NAME, shallow=False
    ), (
        f"docs/generated/{BLOCKER_LEDGER_NAME} disagrees with the run bundles "
        "on this machine - either new runs have not been folded in or a row "
        "was edited. Run `python scripts/export_blocker_ledger.py`."
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

    from kenshi_agent.cli import build_parser

    names: set[str] = set()
    for action in build_parser()._subparsers._group_actions:  # noqa: SLF001
        names.update(action.choices)
    return names


def _dev_subcommands() -> set[str]:
    """Ask the shared dev parser, which is also what the wrapper asks."""

    from kenshi_agent.dev_cli import build_parser

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
    from kenshi_agent.dev_cli import render_reference

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
    # its edges: the prompt writes `python scripts/export_blocker_ledger.py`,
    # and an edge-anchored pattern silently checks nothing there.
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
