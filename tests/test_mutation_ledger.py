"""The ledger's job is to make a stale mutation claim impossible to overlook.

So these tests are written against the ways it could quietly lie: a campaign
that proved nothing ranking as the newest evidence, a module edited after its
campaign still reading `attested`, a rendered row that does not survive being
read back. Each one is a way the real thing went wrong before this existed —
eleven zero-mutant artifacts were the most recent record for eleven shards that
had genuinely been attested hours earlier, and nothing in the repository could
tell the difference.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.mutation_campaign import discover_mutation_batches
from kenshi_agent.mutation_ledger import (
    ATTESTED,
    NEVER,
    SOURCE_CHANGED,
    UNVERIFIED,
    Attestation,
    attestation_from_artifact,
    attestations_from_artifacts,
    export_mutation_ledger,
    merge_attestations,
    normalize_timestamp,
    parse_ledger,
    render_ledger,
    source_digest,
    sources_are_instrumented,
)

MODULES = ("alpha.py", "beta.py")


def _repo(root: Path, modules: dict[str, str] | None = None) -> Path:
    package = root / "src" / "kenshi_agent"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, body in (modules or dict.fromkeys(MODULES, "x = 1")).items():
        (package / name).write_text(body, encoding="utf-8")
    return root


def _batches(root: Path) -> dict:
    return discover_mutation_batches(root / "src" / "kenshi_agent")


def _artifact(
    directory: Path,
    *,
    batch: str,
    total: int,
    actionable: int = 0,
    completed_at: str = "2026-07-28T16:03:50.671164+00:00",
    source_sha256: str | None = "a" * 64,
    name: str | None = None,
) -> Path:
    payload: dict[str, object] = {
        "batch": batch,
        "completed_at": completed_at,
        "source_path": f"src/kenshi_agent/{batch}.py",
        "mutant_pattern": f"kenshi_agent.{batch}.*",
        "counts": {"killed": total - actionable, "survived": actionable},
        "total": total,
        "actionable_mutants": [f"mutant_{index}" for index in range(actionable)],
    }
    if source_sha256 is not None:
        payload["source_sha256"] = source_sha256
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"{completed_at[:19].replace(':', '')}-{batch}.json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _states(text: str) -> dict[str, str]:
    states = {}
    for line in text.splitlines():
        if line.startswith("| `"):
            fields = [field.strip() for field in line.strip()[1:-1].split("|")]
            states[fields[0].strip("`")] = fields[6]
    return states


def test_a_zero_mutant_run_is_not_evidence() -> None:
    """The defect this whole file exists for.

    `mutation_exit_code` already fails closed on a zero-mutant run, so recording
    one as a shard's newest result would let an invalidated cache erase a real
    campaign from the committed record.
    """

    proof = {
        "batch": "alpha",
        "completed_at": "2026-07-28T16:03:50+00:00",
        "total": 12,
        "actionable_mutants": [],
    }
    assert attestation_from_artifact(proof) is not None
    assert attestation_from_artifact({**proof, "total": 0}) is None
    assert attestation_from_artifact({**proof, "total": -1}) is None


def test_a_zero_mutant_run_cannot_displace_a_real_campaign(tmp_path: Path) -> None:
    artifacts = tmp_path / "mutation"
    _artifact(artifacts, batch="alpha", total=429, completed_at="2026-07-28T14:23:21+00:00")
    _artifact(artifacts, batch="alpha", total=0, completed_at="2026-07-28T16:06:43+00:00")

    latest = attestations_from_artifacts(artifacts)

    assert latest["alpha"].mutants == 429
    assert latest["alpha"].attested_at == "2026-07-28T14:23:21Z"


def test_a_single_mutant_campaign_is_still_evidence() -> None:
    """`total <= 0` rejects nothing real; `total <= 1` would discard a whole shard.

    Small shards exist — `campaign` generated 21 mutants and `control.noop` will
    generate far fewer — so the boundary has to sit exactly at zero.
    """

    proof = {
        "batch": "alpha",
        "completed_at": "2026-07-28T16:03:50+00:00",
        "total": 1,
        "actionable_mutants": [],
    }

    attestation = attestation_from_artifact(proof)

    assert attestation is not None
    assert attestation.mutants == 1


def test_one_bad_artifact_does_not_hide_the_ones_after_it(tmp_path: Path) -> None:
    """Scanning must survive junk. `continue` reads like `break` when junk is last.

    Filenames drive the scan order, so each unusable artifact here sorts *before*
    the real campaign it would otherwise conceal.
    """

    artifacts = tmp_path / "mutation"
    artifacts.mkdir(parents=True)
    (artifacts / "1-malformed.json").write_text("{not json", encoding="utf-8")
    (artifacts / "2-wrong-shape.json").write_text('{"batch": "alpha"}', encoding="utf-8")
    _artifact(artifacts, batch="alpha", total=0, name="3-zero-mutants.json")
    _artifact(artifacts, batch="alpha", total=429, name="4-real.json")

    latest = attestations_from_artifacts(artifacts)

    assert latest["alpha"].mutants == 429


def test_the_newest_campaign_wins_regardless_of_scan_order(tmp_path: Path) -> None:
    """Ranking is by recorded time, not by the order the directory happened to yield."""

    artifacts = tmp_path / "mutation"
    _artifact(
        artifacts,
        batch="alpha",
        total=100,
        completed_at="2026-07-28T16:00:00+00:00",
        name="1-newer.json",
    )
    _artifact(
        artifacts,
        batch="alpha",
        total=200,
        completed_at="2026-07-28T09:00:00+00:00",
        name="2-older.json",
    )

    latest = attestations_from_artifacts(artifacts)

    assert latest["alpha"].mutants == 100
    assert latest["alpha"].attested_at == "2026-07-28T16:00:00Z"


def test_a_same_second_rerun_replaces_the_earlier_result(tmp_path: Path) -> None:
    """Ties go to the later scan, so re-running a shard is never a no-op.

    Timestamps are second-resolution, so two campaigns can collide. Preferring the
    earlier one would make a repeat run silently fail to update the ledger.
    """

    artifacts = tmp_path / "mutation"
    stamp = "2026-07-28T16:00:00+00:00"
    _artifact(artifacts, batch="alpha", total=5, completed_at=stamp, name="1.json")
    _artifact(artifacts, batch="alpha", total=6, completed_at=stamp, name="2.json")

    assert attestations_from_artifacts(artifacts)["alpha"].mutants == 6

    old = Attestation("alpha", 5, 5, 0, "2026-07-28T16:00:00Z", "0" * 16)
    new = Attestation("alpha", 6, 6, 0, "2026-07-28T16:00:00Z", "1" * 16)
    assert merge_attestations({"alpha": old}, {"alpha": new})["alpha"] == new


@pytest.mark.parametrize("actionable", [0, 1, 158])
def test_killed_and_open_account_for_every_mutant(actionable: int) -> None:
    attestation = attestation_from_artifact(
        {
            "batch": "alpha",
            "completed_at": "2026-07-28T16:03:50+00:00",
            "total": 2222,
            "actionable_mutants": [f"m{index}" for index in range(actionable)],
        }
    )

    assert attestation is not None
    assert attestation.killed + attestation.open_mutants == attestation.mutants
    assert attestation.open_mutants == actionable


@pytest.mark.parametrize(
    "field, value",
    [
        ("batch", 7),
        ("total", "2222"),
        ("total", None),
        ("actionable_mutants", "kenshi_agent.alpha.x__mutmut_1"),
        ("completed_at", 1769616230),
    ],
)
def test_every_required_field_is_checked_independently(field: str, value: object) -> None:
    """Each payload is valid except for one field, so an `or` weakened to an `and`
    lets it through on the strength of its neighbour.

    These are real shapes: an artifact written by an older tool, a hand-edited file,
    a truncated write. Reading `total` as a string and comparing it to zero raises
    rather than rejects.
    """

    payload = {
        "batch": "alpha",
        "completed_at": "2026-07-28T16:03:50+00:00",
        "total": 2222,
        "actionable_mutants": [],
    }
    assert attestation_from_artifact(payload) is not None

    assert attestation_from_artifact({**payload, field: value}) is None


def test_malformed_artifacts_are_ignored_rather_than_guessed(tmp_path: Path) -> None:
    artifacts = tmp_path / "mutation"
    artifacts.mkdir(parents=True)
    (artifacts / "broken.json").write_text("{not json", encoding="utf-8")
    (artifacts / "list.json").write_text("[]", encoding="utf-8")
    (artifacts / "partial.json").write_text('{"batch": "alpha"}', encoding="utf-8")

    assert attestations_from_artifacts(artifacts) == {}


def test_editing_a_module_flips_its_state(tmp_path: Path) -> None:
    """The gate's whole claim: nobody has to remember to notice."""

    root = _repo(tmp_path)
    batches = _batches(root)
    digest = source_digest(root / "src" / "kenshi_agent" / "alpha.py")
    attestations = {
        "alpha": Attestation("alpha", 429, 429, 0, "2026-07-28T14:23:21Z", digest)
    }

    assert _states(render_ledger(attestations, batches, root))["alpha"] == ATTESTED

    (root / "src" / "kenshi_agent" / "alpha.py").write_text("x = 2", encoding="utf-8")

    assert _states(render_ledger(attestations, batches, root))["alpha"] == SOURCE_CHANGED


def test_an_artifact_without_a_digest_is_unverified_not_attested(tmp_path: Path) -> None:
    """Preserve the counts, refuse to claim they describe this tree."""

    root = _repo(tmp_path)
    artifacts = tmp_path / "mutation"
    _artifact(artifacts, batch="alpha", total=1476, source_sha256=None)

    attestations = attestations_from_artifacts(artifacts)
    rendered = render_ledger(attestations, _batches(root), root)

    assert attestations["alpha"].source_digest == ""
    assert _states(rendered)["alpha"] == UNVERIFIED
    assert "| 1476 | 1476 | 0 |" in rendered


def test_the_whole_document_is_pinned_for_a_known_repository(tmp_path: Path) -> None:
    """The rendered document, exactly, for a repository small enough to state.

    This is the *generated artifact* diffed against its expected form — not an
    assertion about source text. It has to exist separately from the staleness
    gates because those digest the ambient checkout and must stand down inside a
    mutation workspace (`sources_are_instrumented`); without this, every word of
    the explanation would go unmeasured in exactly the run that measures it.

    The explanation is load-bearing: it is what tells a reader that `unverified`
    is not a pass and that `attested` does not promise reproducible numbers.
    """

    root = _repo(tmp_path)
    digest = source_digest(root / "src" / "kenshi_agent" / "alpha.py")
    attestations = {
        "alpha": Attestation("alpha", 10, 9, 1, "2026-07-28T16:00:00Z", digest)
    }

    rendered = render_ledger(attestations, _batches(root), root)

    assert rendered == f"""\
<!-- generated by scripts/export_mutation_ledger.py; edits are overwritten -->

# Mutation attestation

Which mutation campaigns still describe the code in this checkout.
Regenerate with `python scripts/export_mutation_ledger.py`. It reads
`runs/mutation/`, which is machine-local and deliberately not committed —
this file is the committed record of what those artifacts said.

`killed` counts mutants a campaign accounted for; `open` counts the rest,
and a shard is finished only at zero. `state` is recomputed every time this
file is written, by digesting each module and comparing it against the
digest recorded when its campaign ran:

- `attested` — a campaign ran and the module is byte-identical since.
- `source-changed` — the module was edited afterwards, so these numbers
  no longer describe the code that ships.
- `unverified` — the campaign predates source digests in artifacts; the
  tree it attested is unknown. Re-running the shard replaces this.
- `never` — no campaign has ever been recorded for this module.

`attested` does not promise the numbers would reproduce: a mutation result
also depends on the tests, and a changed test suite is not tracked here. It
promises only that the module under test has not moved.

2 shards: 1 attested, 0 unverified, 0 source-changed, 1 never; 1 open mutant \
where the numbers still apply.

| shard | mutants | killed | open | attested | source | state |
| --- | --- | --- | --- | --- | --- | --- |
| `alpha` | 10 | 9 | 1 | 2026-07-28T16:00:00Z | {digest} | attested |
| `beta` | — | — | — | — | — | never |
"""


def test_the_open_mutant_tally_reads_as_english(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    digest = source_digest(root / "src" / "kenshi_agent" / "alpha.py")

    def summary(open_mutants: int) -> str:
        attestation = Attestation(
            "alpha", 10, 10 - open_mutants, open_mutants, "2026-07-28T16:00:00Z", digest
        )
        rendered = render_ledger({"alpha": attestation}, _batches(root), root)
        return next(line for line in rendered.splitlines() if "shards:" in line)

    assert summary(0).endswith("0 open mutants where the numbers still apply.")
    assert summary(1).endswith("1 open mutant where the numbers still apply.")
    assert summary(2).endswith("2 open mutants where the numbers still apply.")


def test_shards_come_from_discovery_not_from_the_ledger(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    attestations = {
        "alpha": Attestation("alpha", 1, 1, 0, "2026-07-28T14:23:21Z", "0" * 16),
        "deleted": Attestation("deleted", 9, 9, 0, "2026-07-28T14:23:21Z", "0" * 16),
    }

    states = _states(render_ledger(attestations, _batches(root), root))

    assert set(states) == {"alpha", "beta"}
    assert states["beta"] == NEVER


def test_parsing_skips_every_non_row_without_abandoning_the_table() -> None:
    """A rendered ledger has no malformed rows, so only a hostile document proves this.

    Each rejected line here sits *between* two real ones, so any `continue` that
    became a `break` truncates the result, and any `or` that became an `and` lets
    a non-row through as an attestation.
    """

    text = "\n".join(
        [
            "| shard | mutants | killed | open | attested | source | state |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| `alpha` | 10 | 9 | 1 | 2026-07-28T16:00:00Z | aaaa | attested |",
            "not a table row at all",
            "| `truncated` | 1 | 1 | 0 | 2026-07-28T16:00:00Z |",
            "| `too` | 1 | 1 | 0 | 2026-07-28T16:00:00Z | a | wide | extra |",
            "| starts but does not end with a pipe",
            "no leading pipe but a trailing one |",
            # Well-formed in every way except the closing pipe. Both halves of the
            # delimiter check have to hold, so neither may carry the other.
            "| `no-close` | 1 | 1 | 0 | 2026-07-28T16:00:00Z | cccc | attested",
            "`no-open` | 1 | 1 | 0 | 2026-07-28T16:00:00Z | cccc | attested |",
            "| `unquoted | 1 | 1 | 0 | 2026-07-28T16:00:00Z | a | attested |",
            "| unquoted` | 1 | 1 | 0 | 2026-07-28T16:00:00Z | a | attested |",
            "| `never-run` | — | — | — | — | — | never |",
            # Unpadded, so trimming one character too many turns `never` into
            # `neve` and the row stops being recognised as a non-attestation.
            "|`never-tight`|—|—|—|—|—|never|",
            "| `omega` | 20 | 20 | 0 | 2026-07-28T17:00:00Z | — | unverified |",
        ]
    )

    parsed = parse_ledger(text)

    assert set(parsed) == {"alpha", "omega"}
    assert parsed["alpha"] == Attestation(
        "alpha", 10, 9, 1, "2026-07-28T16:00:00Z", "aaaa"
    )
    assert parsed["omega"] == Attestation("omega", 20, 20, 0, "2026-07-28T17:00:00Z", "")


@pytest.mark.parametrize("pad", [" ", ""])
def test_parsing_reads_the_exact_cells_it_rendered(pad: str) -> None:
    """Off-by-one slicing of the row's pipes silently shifts every column.

    The unpadded variant is what makes that visible: with the padding the rendered
    form always carries, trimming one extra character only eats a space and
    `strip()` hides it.
    """

    cells = [
        "`alpha`",
        "2222",
        "2064",
        "158",
        "2026-07-28T17:15:23Z",
        "0123456789abcdef",
        "attested",
    ]
    row = "|" + "|".join(f"{pad}{cell}{pad}" for cell in cells) + "|"

    parsed = parse_ledger(row)["alpha"]

    assert parsed.shard == "alpha"
    assert parsed.mutants == 2222
    assert parsed.killed == 2064
    assert parsed.open_mutants == 158
    assert parsed.attested_at == "2026-07-28T17:15:23Z"
    assert parsed.source_digest == "0123456789abcdef"


def test_exporting_creates_missing_parent_directories(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    nested = tmp_path / "docs" / "generated"

    path = export_mutation_ledger(nested, repo_root=root)

    assert path.is_file()


def test_rendering_and_parsing_are_inverse(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"alpha.py": "x = 1", "beta.py": "y = 2", "gamma.py": "z = 3"})
    attestations = {
        "alpha": Attestation("alpha", 2222, 2064, 158, "2026-07-28T17:15:23Z", "b" * 16),
        "beta": Attestation("beta", 1476, 1476, 0, "2026-07-28T16:03:50Z", ""),
    }

    parsed = parse_ledger(render_ledger(attestations, _batches(root), root))

    assert parsed == attestations


def test_normalizing_a_timestamp_is_idempotent_and_utc() -> None:
    assert normalize_timestamp("2026-07-28T16:03:50.671164+00:00") == "2026-07-28T16:03:50Z"
    assert normalize_timestamp("2026-07-28T09:03:50-07:00") == "2026-07-28T16:03:50Z"
    assert normalize_timestamp("2026-07-28T16:03:50Z") == "2026-07-28T16:03:50Z"


def test_later_evidence_wins_and_earlier_evidence_is_kept(tmp_path: Path) -> None:
    old = Attestation("alpha", 1, 1, 0, "2026-07-28T10:00:00Z", "0" * 16)
    new = Attestation("alpha", 9, 8, 1, "2026-07-28T11:00:00Z", "1" * 16)
    other = Attestation("beta", 3, 3, 0, "2026-07-28T09:00:00Z", "2" * 16)

    assert merge_attestations({"alpha": old, "beta": other}, {"alpha": new}) == {
        "alpha": new,
        "beta": other,
    }
    assert merge_attestations({"alpha": new}, {"alpha": old})["alpha"] == new


def test_exporting_without_artifacts_is_the_committed_record_rechecked(
    tmp_path: Path,
) -> None:
    """The staleness gate's exact call: no machine-local input, stable output."""

    root = _repo(tmp_path)
    generated = tmp_path / "generated"
    artifacts = tmp_path / "mutation"
    _artifact(
        artifacts,
        batch="alpha",
        total=429,
        source_sha256=source_digest(root / "src" / "kenshi_agent" / "alpha.py") + "0" * 48,
    )

    first = export_mutation_ledger(
        generated, repo_root=root, artifact_dir=artifacts
    ).read_text(encoding="utf-8")
    ledger = generated / "MUTATION_ATTESTATION.md"
    second = export_mutation_ledger(
        generated, repo_root=root, existing=ledger
    ).read_text(encoding="utf-8")

    assert _states(first)["alpha"] == ATTESTED
    assert second == first


def test_a_module_edited_after_export_makes_the_gate_fail(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    generated = tmp_path / "generated"
    artifacts = tmp_path / "mutation"
    _artifact(
        artifacts,
        batch="alpha",
        total=429,
        source_sha256=source_digest(root / "src" / "kenshi_agent" / "alpha.py") + "0" * 48,
    )
    ledger = export_mutation_ledger(generated, repo_root=root, artifact_dir=artifacts)
    committed = ledger.read_text(encoding="utf-8")

    (root / "src" / "kenshi_agent" / "alpha.py").write_text("x = 99", encoding="utf-8")
    regenerated = export_mutation_ledger(
        generated, repo_root=root, existing=ledger
    ).read_text(encoding="utf-8")

    assert regenerated != committed
    assert _states(regenerated)["alpha"] == SOURCE_CHANGED


def test_a_campaign_artifact_records_the_tree_it_attested(tmp_path: Path) -> None:
    """`mutation_ledger` cannot derive staleness the artifact never recorded."""

    from kenshi_agent.mutation_campaign import (
        MutationBatch,
        MutationSummary,
        _write_run_artifact,
        batch_source_digest,
    )

    root = _repo(tmp_path)
    batch = MutationBatch(
        name="alpha",
        source_path="src/kenshi_agent/alpha.py",
        mutant_pattern="kenshi_agent.alpha.*",
    )
    summary = MutationSummary(counts={"killed": 3}, actionable_mutants=())

    artifact = _write_run_artifact(
        root,
        batch,
        summary,
        source_sha256=batch_source_digest(root, batch),
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert payload["source_sha256"].startswith(
        source_digest(root / "src" / "kenshi_agent" / "alpha.py")
    )
    assert attestation_from_artifact(payload) == Attestation(
        shard="alpha",
        mutants=3,
        killed=3,
        open_mutants=0,
        attested_at=normalize_timestamp(payload["completed_at"]),
        source_digest=source_digest(root / "src" / "kenshi_agent" / "alpha.py"),
    )


def test_every_real_shard_appears_exactly_once() -> None:
    root = Path(__file__).resolve().parents[1]
    ledger = (root / "docs" / "generated" / "MUTATION_ATTESTATION.md").read_text(
        encoding="utf-8"
    )

    states = _states(ledger)

    assert set(states) == set(discover_mutation_batches(root / "src" / "kenshi_agent"))
    assert ledger.count("| `") == len(states)


def test_an_instrumented_tree_is_recognised_by_its_own_layout(tmp_path: Path) -> None:
    """The predicate every byte-comparing gate defers to, tested on explicit paths.

    Deliberately not derived from the ambient repository root: this file also runs
    *inside* a mutation workspace, where an ambient check would assert the very
    thing under test. Explicit paths make it meaningful in both trees, so a mutant
    that always answers yes — silently disabling every staleness gate — dies here.
    """

    assert sources_are_instrumented(tmp_path / ".mutation-workspaces" / "x" / "mutants")
    assert not sources_are_instrumented(tmp_path / "kenshi-agent-env")
    assert not sources_are_instrumented(tmp_path / ".mutation-workspaces" / "mutants2")


def test_the_ledger_never_claims_an_attestation_it_cannot_check() -> None:
    """Every `attested` row in the committed file is re-derived, not trusted."""

    root = Path(__file__).resolve().parents[1]
    if sources_are_instrumented(root):
        pytest.skip("mutmut instruments the sources this check digests")
    ledger = root / "docs" / "generated" / "MUTATION_ATTESTATION.md"
    attestations = parse_ledger(ledger.read_text(encoding="utf-8"))
    batches = discover_mutation_batches(root / "src" / "kenshi_agent")

    for shard, state in _states(ledger.read_text(encoding="utf-8")).items():
        if state != ATTESTED:
            continue
        recorded = attestations[shard].source_digest
        assert recorded == source_digest(root / batches[shard].source_path), (
            f"{shard} is recorded as {ATTESTED} but its source has moved; "
            "run `python scripts/export_mutation_ledger.py`"
        )


def test_unattested_modules_are_listed_rather_than_omitted() -> None:
    """A shard nobody has ever mutated must be visible, not absent."""

    root = Path(__file__).resolve().parents[1]
    ledger = (root / "docs" / "generated" / "MUTATION_ATTESTATION.md").read_text(
        encoding="utf-8"
    )

    states = _states(ledger)
    never = {shard for shard, state in states.items() if state == NEVER}

    assert never, "expected at least one module with no recorded campaign"
    assert f"{len(never)} {NEVER}" in ledger


def test_generated_at_is_absent_so_regeneration_is_deterministic() -> None:
    """A timestamp in the output would make the staleness gate fail every day."""

    root = Path(__file__).resolve().parents[1]
    ledger = (root / "docs" / "generated" / "MUTATION_ATTESTATION.md").read_text(
        encoding="utf-8"
    )

    assert datetime.now(UTC).strftime("%Y-%m-%d") not in ledger.split("| shard |")[0]
