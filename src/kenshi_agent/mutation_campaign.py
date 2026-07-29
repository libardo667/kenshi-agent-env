"""Module-sharded, attended mutation campaigns.

Mutmut instruments every configured source file while discovering which tests
exercise each mutant. A single cache for this project therefore makes an
otherwise bounded module campaign pay for more than thirty thousand unrelated
mutants. Each production module gets an isolated workspace and association
cache instead. Scope stays exhaustive; cost stays resumable and attributable.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":  # pragma: no cover - exercised by Windows development
    import msvcrt
else:  # pragma: no cover - exercised through the public lock on POSIX
    import fcntl

_EXCLUDED_MODULE_FILES = frozenset({"__init__.py", "__main__.py"})
_GENERATED_INPUT_DIRECTORIES = frozenset(
    {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
_GENERATED_INPUT_SUFFIXES = frozenset({".pyc", ".pyo"})
_RESULT_LINE = re.compile(r"^\s+(\S+): (.+)$")
_CLEAN_STATUSES = frozenset({"caught by type check", "killed"})


@dataclass(frozen=True, slots=True)
class MutationBatch:
    name: str
    source_path: str
    mutant_pattern: str


@dataclass(frozen=True, slots=True)
class MutationSummary:
    counts: dict[str, int]
    actionable_mutants: tuple[str, ...]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class MutationCampaignStateError(RuntimeError):
    """A campaign could not produce trustworthy evidence from its workspace."""


def _lock_handle(handle: BinaryIO) -> None:
    """Acquire one non-blocking cross-process byte-range lock."""

    if sys.platform == "win32":  # pragma: no mutate
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # pragma: no mutate
    else:  # pragma: no cover - platform adapter
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: BinaryIO) -> None:
    """Release a lock acquired by `_lock_handle`."""

    if sys.platform == "win32":  # pragma: no mutate
        handle.seek(0)  # pragma: no mutate
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # pragma: no mutate
    else:  # pragma: no cover - platform adapter
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _batch_workspace_lock(workspace: Path) -> Iterator[None]:
    """Fail closed when another process is using the same batch workspace."""

    workspace.mkdir(parents=True, exist_ok=True)
    handle = (workspace / ".campaign.lock").open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _lock_handle(handle)
        except OSError as exc:
            raise MutationCampaignStateError(
                f"mutation batch workspace {workspace.name!r} is already in use"
            ) from exc
        try:
            yield
        finally:
            _unlock_handle(handle)
    finally:
        handle.close()


# These files are an internal UTF-8, human-readable cache format. Tests cover
# every caller's parsed content; codec aliases and indentation are equivalent.
# pragma: no mutate start
def _read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_utf8(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _pretty_json(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


# pragma: no mutate end


def discover_mutation_batches(source_root: Path) -> dict[str, MutationBatch]:
    """Return one non-overlapping mutation batch per production module."""

    package_root = source_root.parent
    batches: dict[str, MutationBatch] = {}
    for source in sorted(source_root.rglob("*.py")):
        if source.name in _EXCLUDED_MODULE_FILES:
            continue
        relative_module = source.relative_to(source_root).with_suffix("")
        name = ".".join(relative_module.parts)
        source_path = source.relative_to(package_root.parent).as_posix()
        batch = MutationBatch(
            name=name,
            source_path=source_path,
            mutant_pattern=f"kenshi_agent.{name}.*",
        )
        if name in batches:
            raise ValueError(  # mutation: diagnostic-only
                f"duplicate mutation batch {name!r}"
            )
        batches[name] = batch
    return batches


def _render_batch_pyproject(original: str, batch: MutationBatch) -> str:
    parsed = tomllib.loads(original)
    try:
        mutmut_config = parsed["tool"]["mutmut"]
    except KeyError as exc:
        raise ValueError(  # mutation: diagnostic-only
            "pyproject.toml has no [tool.mutmut] table"
        ) from exc
    if "only_mutate" in mutmut_config:
        # The wording is diagnostic; rejection is tested.
        # pragma: no mutate start
        raise ValueError(
            "The root mutmut configuration must stay project-wide; "
            "module scope belongs to the batch workspace."
        )
        # pragma: no mutate end
    # Valid TOML has one such table; first-vs-last partition is not a behavior.
    head, marker, tail = original.partition("[tool.mutmut]")  # pragma: no mutate
    if not marker:
        raise ValueError(  # mutation: diagnostic-only
            "pyproject.toml has no [tool.mutmut] table"
        )
    rendered = head + marker + f"\nonly_mutate = [{json.dumps(batch.source_path)}]" + tail
    rendered_config = tomllib.loads(rendered)["tool"]["mutmut"]
    if rendered_config["only_mutate"] != [batch.source_path]:
        raise ValueError(  # mutation: diagnostic-only
            "failed to render exact module mutation scope"
        )
    return rendered


def _configured_project_anchors(pyproject: dict[str, object]) -> set[str]:
    try:
        mutmut_config = pyproject["tool"]["mutmut"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise ValueError(  # mutation: diagnostic-only
            "pyproject.toml has no [tool.mutmut] table"
        ) from exc
    if not isinstance(mutmut_config, dict):
        raise ValueError(  # mutation: diagnostic-only
            "[tool.mutmut] must be a table"
        )
    configured: list[object] = []
    for key in ("source_paths", "also_copy"):
        paths = mutmut_config.get(key, [])
        if not isinstance(paths, list):
            raise ValueError(  # mutation: diagnostic-only
                f"mutmut {key} must be a list"
            )
        configured.extend(paths)
    configured.append("tests/")
    anchors: set[str] = set()
    for raw_path in configured:
        if not isinstance(raw_path, str):
            raise ValueError(  # mutation: diagnostic-only
                "mutmut project paths must be strings"
            )
        path = Path(raw_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError(  # mutation: diagnostic-only
                "mutmut project paths must stay inside the repository"
            )
        anchors.add(path.parts[0])
    return anchors


def _input_digest(repo_root: Path, anchors: set[str]) -> str:
    digest = hashlib.sha256()
    paths: list[Path] = [repo_root / "pyproject.toml"]
    for anchor in sorted(anchors):
        path = repo_root / anchor
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in sorted(path.rglob("*"))
                if candidate.is_file()
                and candidate.suffix not in _GENERATED_INPUT_SUFFIXES
                and not any(
                    part in _GENERATED_INPUT_DIRECTORIES
                    for part in candidate.relative_to(path).parts[:-1]
                )
            )
        elif path.is_file():
            paths.append(path)
    for path in paths:
        relative = path.relative_to(repo_root).as_posix()
        # Hash framing is an internal choice; change-detection invariants are tested.
        digest.update(relative.encode("utf-8"))  # pragma: no mutate
        digest.update(b"\0")  # pragma: no mutate
        digest.update(path.read_bytes())
        digest.update(b"\0")  # pragma: no mutate
    return digest.hexdigest()


def _input_fingerprint(repo_root: Path) -> dict[str, str]:
    original = _read_utf8(repo_root / "pyproject.toml")
    anchors = _configured_project_anchors(tomllib.loads(original))
    return {"digest": _input_digest(repo_root, anchors)}


def _assert_batch_inputs_unchanged(repo_root: Path, workspace: Path) -> None:
    """Refuse evidence when a symlinked campaign input changed mid-run."""

    try:
        expected = json.loads(
            _read_utf8(workspace / "input-fingerprint.json")
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise MutationCampaignStateError(
            "mutation input fingerprint vanished or became unreadable"
        ) from exc
    actual = _input_fingerprint(repo_root)
    if expected != actual:
        raise MutationCampaignStateError(
            "project inputs changed while mutmut was running; "
            "discard this run and retry from a stable tree"
        )


def _ensure_project_symlink(repo_root: Path, workspace: Path, anchor: str) -> None:
    source = repo_root / anchor
    if not source.exists():
        return
    destination = workspace / anchor
    if destination.is_symlink():
        if destination.resolve() != source.resolve():
            # The wording is diagnostic; rejection is tested.
            # pragma: no mutate start
            raise ValueError(f"mutation workspace link {destination} points outside the project")
            # pragma: no mutate end
        return
    if destination.exists():
        # The wording is diagnostic; rejection is tested.
        # pragma: no mutate start
        raise ValueError(f"mutation workspace path {destination} is not a managed symlink")
        # pragma: no mutate end
    destination.symlink_to(source, target_is_directory=source.is_dir())


def _discard_generated_tree(workspace: Path) -> None:
    """Invalidate copied inputs, bytecode, associations, and mutants together."""

    mutants = workspace / "mutants"
    if mutants.is_symlink():
        raise MutationCampaignStateError(
            "mutation generated tree is an unmanaged symlink"  # mutation: diagnostic-only
        )
    if mutants.exists():
        shutil.rmtree(mutants)


def _batch_cache_is_complete(workspace: Path, batch: MutationBatch) -> bool:
    """Reject partial mutmut output left by an interrupted generation pass."""

    mutants = workspace / "mutants"
    stats = mutants / "mutmut-stats.json"
    generated_source = mutants / batch.source_path
    generated_metadata = Path(f"{generated_source}.meta")
    cache_paths = (stats, generated_source, generated_metadata)
    if not any(path.exists() for path in cache_paths):
        return True
    if not all(path.is_file() for path in cache_paths):
        return False
    if generated_source.stat().st_size == 0:
        return False
    try:
        metadata = json.loads(_read_utf8(generated_metadata))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    exit_codes = metadata.get("exit_code_by_key")
    return isinstance(exit_codes, dict) and bool(exit_codes)


def prepare_batch_workspace(repo_root: Path, batch: MutationBatch) -> Path:
    """Prepare one isolated mutmut cache and invalidate stale test associations."""

    root_pyproject = repo_root / "pyproject.toml"
    original = _read_utf8(root_pyproject)
    parsed = tomllib.loads(original)
    anchors = _configured_project_anchors(parsed)
    workspace = repo_root / ".mutation-workspaces" / batch.name
    workspace.mkdir(parents=True, exist_ok=True)
    for anchor in sorted(anchors):
        _ensure_project_symlink(repo_root, workspace, anchor)

    rendered = _render_batch_pyproject(original, batch)
    _write_utf8(workspace / "pyproject.toml", rendered)

    fingerprint_path = workspace / "input-fingerprint.json"
    fingerprint = {"digest": _input_digest(repo_root, anchors)}
    # Any non-fingerprint sentinel invalidates the cache on a missing/corrupt file.
    previous: object = None  # pragma: no mutate
    try:
        previous = json.loads(_read_utf8(fingerprint_path))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if previous != fingerprint or not _batch_cache_is_complete(workspace, batch):
        _discard_generated_tree(workspace)
    _write_utf8(fingerprint_path, _pretty_json(fingerprint))
    return workspace


def parse_mutmut_results(output: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for line in output.splitlines():
        match = _RESULT_LINE.match(line)
        if match is None:
            continue
        mutant, status = match.groups()
        if mutant in results:
            raise ValueError(  # mutation: diagnostic-only
                f"duplicate mutant result for {mutant}"
            )
        results[mutant] = status
    return results


def summarize_results(
    results: dict[str, str],
    mutant_pattern: str,
) -> MutationSummary:
    selected = {
        mutant: status
        for mutant, status in results.items()
        if fnmatch.fnmatch(mutant, mutant_pattern)
    }
    counts = dict(sorted(Counter(selected.values()).items()))
    actionable = tuple(
        sorted(mutant for mutant, status in selected.items() if status not in _CLEAN_STATUSES)
    )
    return MutationSummary(counts=counts, actionable_mutants=actionable)


def mutation_exit_code(
    summary: MutationSummary,
    *,
    allow_actionable: bool,
) -> int:
    """Fail closed when a campaign has no proof or leaves mutants unattended."""

    if summary.total == 0:
        return 1
    return int(bool(summary.actionable_mutants) and not allow_actionable)


def _mutmut_executable() -> str:
    adjacent = Path(sys.executable).with_name("mutmut")
    return str(adjacent) if adjacent.exists() else "mutmut"


def _read_batch_results(
    workspace: Path,
    batch: MutationBatch,
) -> MutationSummary:
    completed = subprocess.run(
        [_mutmut_executable(), "results", "--all", "true"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    return summarize_results(
        parse_mutmut_results(completed.stdout),
        batch.mutant_pattern,
    )


def _artifact_path(
    artifact_dir: Path,
    artifact_stem: str,
    collision_index: int,
) -> Path:
    suffix = "" if collision_index == 0 else f"-{collision_index}"
    return artifact_dir / f"{artifact_stem}{suffix}.json"


def batch_source_digest(repo_root: Path, batch: MutationBatch) -> str:
    """Which exact source these results describe.

    Without this an artifact cannot be told apart from the same artifact taken
    four commits later, so `mutation_ledger` could not report a module edited
    since its campaign. Fails closed on a missing source: a campaign that cannot
    name its tree has attested nothing.
    """

    return hashlib.sha256((repo_root / batch.source_path).read_bytes()).hexdigest()


def _write_run_artifact(
    repo_root: Path,
    batch: MutationBatch,
    summary: MutationSummary,
    *,
    source_sha256: str,
) -> Path:
    completed_at = datetime.now(UTC)
    artifact_dir = repo_root / "runs" / "mutation"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = completed_at.strftime("%Y%m%dT%H%M%SZ")
    artifact_stem = f"{timestamp}-{batch.name.replace('.', '-')}"
    payload = {
        "batch": batch.name,
        "completed_at": completed_at.isoformat(),
        "source_path": batch.source_path,
        "mutant_pattern": batch.mutant_pattern,
        "source_sha256": source_sha256,
        "counts": summary.counts,
        "total": summary.total,
        "actionable_mutants": summary.actionable_mutants,
    }
    collision_index = 0
    artifact = _artifact_path(artifact_dir, artifact_stem, collision_index)
    while True:
        try:
            with artifact.open("x", encoding="utf-8") as handle:
                handle.write(_pretty_json(payload))
        except FileExistsError:
            collision_index += 1
            next_artifact = _artifact_path(
                artifact_dir,
                artifact_stem,
                collision_index,
            )
            if next_artifact == artifact:
                raise RuntimeError(  # mutation: diagnostic-only
                    "mutation artifact collision resolution repeated a candidate"
                ) from None
            artifact = next_artifact
            continue
        return artifact


def _print_summary(batch: MutationBatch, summary: MutationSummary) -> None:
    counts = " ".join(f"{status}={count}" for status, count in summary.counts.items())
    print(f"{batch.name}: total={summary.total} {counts}".rstrip())
    for mutant in summary.actionable_mutants:
        print(f"  attend: {mutant}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Argparse's declarative grammar is acceptance-tested through main. Mutating help
# capitalization and framework call syntax is noise rather than behavioral signal.
# pragma: no mutate start
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run module-sharded, attended mutmut campaigns.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List every production-module batch.")
    run_parser = subparsers.add_parser("run", help="Run and summarize one batch.")
    run_parser.add_argument("batch")
    run_parser.add_argument("--max-children", type=int, default=6)
    run_parser.add_argument(
        "--allow-actionable",
        action="store_true",
        help="Return success for an explicitly attended baseline with open results.",
    )
    results_parser = subparsers.add_parser(
        "results",
        help="Summarize the cached results for one batch.",
    )
    results_parser.add_argument("batch")
    results_parser.add_argument(
        "--allow-actionable",
        action="store_true",
        help="Return success for an explicitly attended baseline with open results.",
    )
    return parser


# pragma: no mutate end


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    batches = discover_mutation_batches(repo_root / "src" / "kenshi_agent")
    if args.command == "list":
        for batch in batches.values():
            print(f"{batch.name}\t{batch.source_path}")
        return 0
    try:
        batch = batches[args.batch]
    except KeyError:
        # The wording is diagnostic; rejection is tested.
        # pragma: no mutate start
        parser.error(
            f"unknown mutation batch {args.batch!r}; run `kenshi-mutate list` for exact names"
        )
        # pragma: no mutate end

    workspace = repo_root / ".mutation-workspaces" / batch.name
    try:
        with _batch_workspace_lock(workspace):
            workspace = prepare_batch_workspace(repo_root, batch)
            if args.command == "run":
                if args.max_children < 1:
                    parser.error(  # mutation: diagnostic-only
                        "--max-children must be positive"
                    )
                completed = subprocess.run(
                    [
                        _mutmut_executable(),
                        "run",
                        "--max-children",
                        str(args.max_children),
                        batch.mutant_pattern,
                    ],
                    cwd=workspace,
                    check=False,
                )
                if completed.returncode != 0:
                    return completed.returncode
                _assert_batch_inputs_unchanged(repo_root, workspace)

            summary = _read_batch_results(workspace, batch)
            artifact = _write_run_artifact(
                repo_root,
                batch,
                summary,
                source_sha256=batch_source_digest(repo_root, batch),
            )
            _print_summary(batch, summary)
            print(f"artifact: {artifact.relative_to(repo_root)}")
            return mutation_exit_code(
                summary,
                allow_actionable=args.allow_actionable,
            )
    except MutationCampaignStateError as exc:
        print(
            f"mutation campaign refused: {exc}",
            file=sys.stderr,
        )
        return 2


# Console-script behavior is exercised through main; this is import-time plumbing.
if __name__ == "__main__":  # pragma: no mutate
    raise SystemExit(main())
