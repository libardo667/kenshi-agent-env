"""Mechanical gates against documentation drift and sprawl.

Prose asking agents to be concise loses to the next loop's instincts. These
checks do not ask. Every document must be one of four shapes, must stay under a
hard line cap, and must fail the build rather than quietly lie when the thing it
describes has moved.

A document is legitimate only if it is:

* `docs/ADR_*.md` — a durable decision, written once, superseded not edited;
* `docs/GUIDE_*.md` — a procedure or wire contract an implementer needs, which
  restates no code and has no other home;
* `docs/REPORT_<YYYYMMDD>_<topic>.md` — a dated analysis record, written once,
  superseded by a later report and never by editing an earlier one;
* `docs/generated/*` — emitted from the code that is its source of truth.

Anything else is either restating code (generate it) or restating history (the
commit log and `runs/` already hold it). `LEGACY_DOCS` is deliberately empty: the
2026-07-26 cleanup deleted or converted all 25 grandfathered files. Re-adding an
entry is admitting a new exception, so do it loudly or not at all.

The shape rules are pure functions of a path so the suite can prove they reject
what they claim to reject, by running a real temporary file through the same
gate rather than by restating the pattern in an assertion.
"""

from __future__ import annotations

import filecmp
import re
import subprocess
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
DOC_LINE_CAP = 120
GENERATED_DOCS = ROOT / "docs" / "generated"
PRE_COMMIT_HOOK = ROOT / ".githooks" / "pre-commit"

# A report is dated and slugged so ordering is filename-visible and two reports
# written on one day cannot collide. `_` separates the fields; the topic uses
# `-` so the split is unambiguous.
REPORT_NAME = re.compile(r"^REPORT_\d{8}_[a-z0-9-]+\.md$")

# Root-level prose. Every file here is either capped or listed as exempt with a
# reason, and `test_every_root_prose_file_is_capped_or_exempt` fails on any root
# document that is in neither — being uncapped must be a decision, not an
# omission. Lower a ceiling freely; raising one is the loud part.
ROOT_DOC_CAPS: dict[str, int] = {
    "README.md": DOC_LINE_CAP,
    "ARCHITECTURE.md": DOC_LINE_CAP,
    "STATUS.md": DOC_LINE_CAP,
    "SECURITY_AND_SAFETY.md": DOC_LINE_CAP,
    # Durable method only: volatile blockers stay in generated/run state.
    # Ratchet downward; growth must remain a deliberate test change.
    "LOOP-PROMPT.md": 230,
}

# Not root documents, but the same reasoning: a step-by-step procedure someone
# follows at the FCS keyboard cannot be split without making them chase it
# across files, and shortening it is how build guides start lying. Same ratchet
# as above: growing one means raising its number here, where a reviewer sees it.
PROCEDURE_DOC_CAPS: dict[str, int] = {
    "docs/GUIDE_LADLE_START.md": 236,
}

ROOT_DOC_EXEMPTIONS: dict[str, str] = {
    "CHANGELOG.md": "an append-only history is meant to grow",
}

LEGACY_DOCS: dict[str, int] = {}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _document_shape_error(relative: str) -> str | None:
    """Why `relative` is not an accepted document shape, or `None` if it is.

    The single source of the rule. `.githooks/pre-commit` is its other encoding
    and `test_the_hook_and_the_suite_accept_the_same_paths` holds them together.
    """

    name = relative.rsplit("/", 1)[-1]
    if relative.startswith("docs/generated/"):
        return None
    if name.startswith(("ADR_", "GUIDE_")):
        return None
    if name.startswith("REPORT_"):
        if REPORT_NAME.match(name):
            return None
        return f"{relative} is not REPORT_<YYYYMMDD>_<lowercase-topic>.md"
    return f"{relative} is not a decision record, report, guide, or generated output"


def _rejected_documents(root: Path) -> list[str]:
    return sorted(
        error
        for path in root.glob("docs/**/*.md")
        if path.is_file() and path.relative_to(root).as_posix() not in LEGACY_DOCS
        for error in [_document_shape_error(path.relative_to(root).as_posix())]
        if error is not None
    )


def _ceiling(relative: str) -> int:
    if relative in PROCEDURE_DOC_CAPS:
        return PROCEDURE_DOC_CAPS[relative]
    return ROOT_DOC_CAPS.get(relative, DOC_LINE_CAP)


def _oversized_documents(root: Path) -> dict[str, int]:
    paths = sorted(root.glob("docs/**/*.md"))
    paths += [root / name for name in ROOT_DOC_CAPS]
    oversized: dict[str, int] = {}
    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in LEGACY_DOCS:
            continue
        # A generated document's length is a function of Kenshi's surface, not
        # of prose sprawl. Capping it means the report must get less informative
        # as the game's affordances grow, which is the metric fighting the goal:
        # the binding parity report was already being trimmed to fit. Staleness
        # is the guard that matters for derived output, and
        # `test_generated_docs_are_not_stale` already holds it.
        if relative.startswith("docs/generated/"):
            continue
        lines = _line_count(path)
        if lines > _ceiling(relative):
            oversized[relative] = lines
    return oversized


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
    oversized = _oversized_documents(ROOT)
    assert not oversized, (
        f"documentation over its ceiling: {oversized}. Split it, cut it, or move "
        "the narrative into the commit message that carries the evidence."
    )


def test_every_root_prose_file_is_capped_or_exempt() -> None:
    """An uncapped root document must be a stated decision, not an omission."""

    unaccounted = sorted(
        path.name
        for path in ROOT.glob("*.md")
        if path.name not in ROOT_DOC_CAPS and path.name not in ROOT_DOC_EXEMPTIONS
    )
    assert not unaccounted, (
        f"root documents with neither a ceiling nor a stated exemption: "
        f"{unaccounted}. Add it to ROOT_DOC_CAPS, or to ROOT_DOC_EXEMPTIONS with "
        "the reason it is allowed to grow."
    )
    unexplained = sorted(name for name, why in ROOT_DOC_EXEMPTIONS.items() if not why.strip())
    assert not unexplained, f"exemptions without a stated reason: {unexplained}"


def test_docs_are_decision_records_reports_guides_or_generated() -> None:
    """A new doc has exactly four legitimate shapes; everything else is sprawl."""

    rejected = _rejected_documents(ROOT)
    assert not rejected, (
        f"unclassified docs: {rejected}. A doc must be a durable decision record "
        "(docs/ADR_*.md), a dated analysis record "
        "(docs/REPORT_<YYYYMMDD>_<topic>.md), a procedure or wire contract "
        "(docs/GUIDE_*.md), or generated output (docs/generated/), or it will "
        "drift."
    )


def test_a_malformed_report_filename_is_rejected(tmp_path: Path) -> None:
    """Prove the shape gate rejects, by running real files through it.

    An assertion restating `REPORT_NAME` would pass even if nothing called it.
    """

    docs = tmp_path / "docs"
    docs.mkdir()
    accepted = "REPORT_20260727_external-review.md"
    malformed = (
        "REPORT_2026-07-27_dashes.md",  # dated, but not YYYYMMDD
        "REPORT_20260727_Mixed_Case.md",  # topic is not a lowercase slug
        "REPORT_20260727.md",  # no topic at all
        "REPORT_external_review.md",  # no date at all
        "NOTES.md",  # not a shape at all
    )
    for name in (accepted, *malformed):
        (docs / name).write_text("# x\n", encoding="utf-8")

    rejected = _rejected_documents(tmp_path)

    assert len(rejected) == len(malformed), rejected
    for name in malformed:
        assert any(name in error for error in rejected), f"{name} was accepted"
    assert not any(accepted in error for error in rejected), (
        f"{accepted} is well formed but was rejected"
    )


def test_an_oversized_report_is_rejected(tmp_path: Path) -> None:
    """A report is prose like any other and gets no cap relief."""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "REPORT_20260727_at-the-cap.md").write_text(
        "\n".join("x" for _ in range(DOC_LINE_CAP)) + "\n", encoding="utf-8"
    )
    (docs / "REPORT_20260727_over-the-cap.md").write_text(
        "\n".join("x" for _ in range(DOC_LINE_CAP + 1)) + "\n", encoding="utf-8"
    )

    oversized = _oversized_documents(tmp_path)

    assert oversized == {"docs/REPORT_20260727_over-the-cap.md": DOC_LINE_CAP + 1}


def _hook_rejects(tmp_path: Path, relative: str) -> bool:
    """Stage one added document in a scratch repo and run the real hook on it."""

    repo = tmp_path / relative.replace("/", "_")
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    document = repo / relative
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("# scratch\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    completed = subprocess.run(
        ["bash", str(PRE_COMMIT_HOOK)], cwd=repo, capture_output=True, text=True
    )
    return completed.returncode != 0


def _hook_rejects_report_edit(tmp_path: Path) -> bool:
    """Commit, modify, and stage one report before running the real hook."""

    repo = tmp_path / "report-edit"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    relative = "docs/REPORT_20260727_write-once.md"
    document = repo / relative
    document.parent.mkdir(parents=True)
    document.write_text("# original\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Doc Gate",
            "-c",
            "user.email=doc-gate@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "seed report",
        ],
        cwd=repo,
        check=True,
    )
    document.write_text("# edited\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    completed = subprocess.run(
        ["bash", str(PRE_COMMIT_HOOK)], cwd=repo, capture_output=True, text=True
    )
    return completed.returncode != 0


def test_reports_are_write_once_in_the_real_hook(tmp_path: Path) -> None:
    assert _hook_rejects_report_edit(tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        "docs/ADR_SOMETHING.md",
        "docs/GUIDE_SOMETHING.md",
        "docs/generated/SOMETHING.md",
        "docs/REPORT_20260727_external-review.md",
        "docs/REPORT_20260727_a.md",
        "docs/REPORT_2026-07-27_dashes.md",
        "docs/REPORT_20260727_Mixed_Case.md",
        "docs/REPORT_20260727.md",
        "docs/REPORT_external_review.md",
        "docs/REPORTING_STANDARDS.md",
        "docs/NOTES.md",
    ],
)
def test_the_hook_and_the_suite_accept_the_same_paths(tmp_path: Path, relative: str) -> None:
    """Two encodings of one rule must not drift apart.

    The hook exists to give an agent the verdict before it writes 400 lines. A
    hook that is more permissive than CI teaches the agent the wrong rule.
    """

    assert _hook_rejects(tmp_path, relative) == (_document_shape_error(relative) is not None), (
        f"{relative}: .githooks/pre-commit and _document_shape_error disagree"
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
    sources += [ROOT / name for name in (*ROOT_DOC_CAPS, *ROOT_DOC_EXEMPTIONS)]
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


def test_generated_documents_are_not_length_capped() -> None:
    """Derived output is guarded by staleness, never by prose length.

    `docs/generated/` was already exempt from the naming rule and not from the
    line cap, which reads as an oversight rather than a decision. The binding
    parity report hit the ceiling and the fix on offer was to print fewer
    findings — a length limit deleting evidence it was never meant to govern.
    """

    generated = sorted((ROOT / "docs" / "generated").glob("*.md"))
    assert generated, "expected generated documents to exist"

    oversized = _oversized_documents(ROOT)
    assert not any(name.startswith("docs/generated/") for name in oversized)
    # And at least one of them is genuinely over the prose cap, so this is not
    # vacuously true.
    assert any(_line_count(path) > DOC_LINE_CAP for path in generated)


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
    """The `./dev` wrapper's own usage line is its list of commands."""

    for line in (ROOT / "dev").read_text(encoding="utf-8").splitlines():
        match = re.search(r"usage: \./dev \{([^}]+)\}", line)
        if match:
            return set(match.group(1).split("|"))
    raise AssertionError("./dev no longer prints a usage line to derive from")


def test_authored_docs_only_name_commands_that_exist() -> None:
    """No authored document may send a reader to run something removed.

    `LOOP-PROMPT.md` told every agent to run `kenshi-agent
    aggregate-affordances` long after that command left the CLI, and once that
    was found, `README.md` and a guide turned out to name it too. Prose is the
    one place nobody diffs against the code, because it does not look like an
    interface - so the drift stays invisible until an agent burns a turn on it.

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
