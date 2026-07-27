from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import kenshi_agent.mutation_campaign as campaign
from kenshi_agent.mutation_campaign import (
    MutationBatch,
    MutationSummary,
    discover_mutation_batches,
    mutation_exit_code,
    parse_mutmut_results,
    prepare_batch_workspace,
    summarize_results,
)


def test_discovery_partitions_every_production_module_once(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "kenshi_agent"
    (source_root / "env").mkdir(parents=True)
    for relative in (
        "__init__.py",
        "__main__.py",
        "planning.py",
        "env/__init__.py",
        "env/live.py",
    ):
        (source_root / relative).write_text("", encoding="utf-8")

    batches = discover_mutation_batches(source_root)

    assert batches == {
        "env.live": MutationBatch(
            name="env.live",
            source_path="src/kenshi_agent/env/live.py",
            mutant_pattern="kenshi_agent.env.live.*",
        ),
        "planning": MutationBatch(
            name="planning",
            source_path="src/kenshi_agent/planning.py",
            mutant_pattern="kenshi_agent.planning.*",
        ),
    }


def test_workspace_scopes_mutmut_without_copying_project_inputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "kenshi_agent").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "config").mkdir()
    (repo / "src" / "kenshi_agent" / "planning.py").write_text(
        "def plan(): return True\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_planning.py").write_text("", encoding="utf-8")
    (repo / "config" / "default.yaml").write_text("{}\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        """
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mutmut]
source_paths = ["src/kenshi_agent/"]
pytest_add_cli_args_test_selection = ["tests/"]
also_copy = ["config/"]
""".lstrip(),
        encoding="utf-8",
    )
    batch = MutationBatch(
        name="planning",
        source_path="src/kenshi_agent/planning.py",
        mutant_pattern="kenshi_agent.planning.*",
    )

    workspace = prepare_batch_workspace(repo, batch)

    assert workspace == repo / ".mutation-workspaces" / "planning"
    workspace_config = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))
    assert workspace_config["tool"]["mutmut"]["only_mutate"] == ["src/kenshi_agent/planning.py"]
    assert (workspace / "src").is_symlink()
    assert (workspace / "tests").is_symlink()
    assert (workspace / "config").is_symlink()
    fingerprint = json.loads((workspace / "input-fingerprint.json").read_text(encoding="utf-8"))
    assert fingerprint["digest"]


def test_workspace_invalidates_test_associations_when_inputs_change(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "kenshi_agent").mkdir(parents=True)
    (repo / "tests").mkdir()
    source = repo / "src" / "kenshi_agent" / "memory.py"
    test = repo / "tests" / "test_memory.py"
    source.write_text("def recall(): return []\n", encoding="utf-8")
    test.write_text("def test_recall(): pass\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        """
[tool.mutmut]
source_paths = ["src/kenshi_agent/"]
pytest_add_cli_args_test_selection = ["tests/"]
""".lstrip(),
        encoding="utf-8",
    )
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    workspace = prepare_batch_workspace(repo, batch)
    stats = workspace / "mutants" / "mutmut-stats.json"
    stats.parent.mkdir()
    stats.write_text("{}\n", encoding="utf-8")

    test.write_text("def test_recall(): assert True\n", encoding="utf-8")
    prepare_batch_workspace(repo, batch)

    assert not stats.exists()


def test_result_summary_is_exactly_scoped_to_one_batch() -> None:
    output = """
    kenshi_agent.memory.x_recall__mutmut_1: killed
    kenshi_agent.memory.x_recall__mutmut_2: survived
    kenshi_agent.memory.x_recall__mutmut_3: timeout
    kenshi_agent.planning.x_plan__mutmut_1: survived
"""
    parsed = parse_mutmut_results(output)

    summary = summarize_results(parsed, "kenshi_agent.memory.*")

    assert summary.counts == {"killed": 1, "survived": 1, "timeout": 1}
    assert summary.actionable_mutants == (
        "kenshi_agent.memory.x_recall__mutmut_2",
        "kenshi_agent.memory.x_recall__mutmut_3",
    )


@pytest.mark.parametrize(
    "status",
    [
        "check was interrupted by user",
        "future mutmut status",
        "no tests",
        "not checked",
        "segfault",
        "skipped",
        "suspicious",
        "survived",
        "timeout",
    ],
)
def test_every_result_other_than_a_proved_kill_is_actionable(status: str) -> None:
    mutant = "kenshi_agent.memory.x_recall__mutmut_1"

    summary = summarize_results({mutant: status}, "kenshi_agent.memory.*")

    assert summary.actionable_mutants == (mutant,)


@pytest.mark.parametrize("status", ["caught by type check", "killed"])
def test_proved_kill_statuses_are_clean(status: str) -> None:
    summary = summarize_results(
        {"kenshi_agent.memory.x_recall__mutmut_1": status},
        "kenshi_agent.memory.*",
    )

    assert summary.actionable_mutants == ()


def test_actionable_mutants_fail_the_campaign_unless_explicitly_attended() -> None:
    actionable = summarize_results(
        {"kenshi_agent.memory.x_recall__mutmut_1": "survived"},
        "kenshi_agent.memory.*",
    )
    clean = summarize_results(
        {"kenshi_agent.memory.x_recall__mutmut_1": "killed"},
        "kenshi_agent.memory.*",
    )
    empty = summarize_results({}, "kenshi_agent.memory.*")

    assert mutation_exit_code(actionable, allow_actionable=False) == 1
    assert mutation_exit_code(actionable, allow_actionable=True) == 0
    assert mutation_exit_code(clean, allow_actionable=False) == 0
    assert mutation_exit_code(empty, allow_actionable=False) == 1
    assert mutation_exit_code(empty, allow_actionable=True) == 1


def test_result_parser_rejects_duplicate_mutant_rows() -> None:
    output = """
    kenshi_agent.memory.x_recall__mutmut_1: killed
    kenshi_agent.memory.x_recall__mutmut_1: survived
"""

    with pytest.raises(ValueError, match="duplicate mutant result"):
        parse_mutmut_results(output)


def test_batch_rendering_preserves_root_config_and_rejects_invalid_roots() -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    original = """
[project]
name = "example"

[tool.mutmut]
source_paths = ["src/kenshi_agent/"]
also_copy = ["docs/"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".lstrip()

    rendered = campaign._render_batch_pyproject(original, batch)
    parsed = tomllib.loads(rendered)

    assert parsed["project"] == {"name": "example"}
    assert parsed["tool"]["mutmut"] == {
        "only_mutate": ["src/kenshi_agent/memory.py"],
        "source_paths": ["src/kenshi_agent/"],
        "also_copy": ["docs/"],
    }
    assert parsed["tool"]["pytest"]["ini_options"] == {"testpaths": ["tests"]}
    with pytest.raises(ValueError, match="no \\[tool.mutmut\\] table"):
        campaign._render_batch_pyproject('[project]\nname = "missing"\n', batch)
    with pytest.raises(ValueError, match="root mutmut configuration"):
        campaign._render_batch_pyproject(
            '[tool.mutmut]\nonly_mutate = ["already-scoped.py"]\n',
            batch,
        )


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"tool": {}},
        {"tool": {"mutmut": []}},
        {"tool": {"mutmut": {"source_paths": "src"}}},
        {"tool": {"mutmut": {"source_paths": [42]}}},
        {"tool": {"mutmut": {"source_paths": ["/outside"]}}},
        {"tool": {"mutmut": {"source_paths": ["../outside"]}}},
        {"tool": {"mutmut": {"also_copy": [None]}}},
    ],
)
def test_project_anchor_configuration_fails_closed(config: object) -> None:
    with pytest.raises(ValueError):
        campaign._configured_project_anchors(config)  # type: ignore[arg-type]


def test_project_anchor_configuration_unions_top_level_paths() -> None:
    config = {
        "tool": {
            "mutmut": {
                "source_paths": ["src/kenshi_agent/", "plugins/game.py"],
                "also_copy": ["docs/", "pyproject-extra.toml"],
            }
        }
    }

    assert campaign._configured_project_anchors(config) == {
        "docs",
        "plugins",
        "pyproject-extra.toml",
        "src",
        "tests",
    }


def test_input_digest_covers_names_contents_and_nested_anchors(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "nested").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    source = repo / "src" / "nested" / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    first = campaign._input_digest(repo, {"src"})

    source.write_text("value = 2\n", encoding="utf-8")
    changed_contents = campaign._input_digest(repo, {"src"})
    source.rename(repo / "src" / "nested" / "renamed.py")
    changed_name = campaign._input_digest(repo, {"src"})
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    changed_config = campaign._input_digest(repo, {"src"})
    standalone = repo / "scenario.lock"
    standalone.write_text("first\n", encoding="utf-8")
    standalone_first = campaign._input_digest(repo, {"src", "scenario.lock"})
    standalone.write_text("second\n", encoding="utf-8")
    standalone_second = campaign._input_digest(repo, {"src", "scenario.lock"})

    assert len({first, changed_contents, changed_name, changed_config}) == 4
    assert standalone_first != standalone_second


@pytest.mark.parametrize(
    ("anchor", "is_directory"),
    [("tests", True), ("config.toml", False)],
)
def test_managed_symlink_preserves_the_source_kind(
    anchor: str,
    is_directory: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    repo.mkdir()
    workspace.mkdir()
    source = repo / anchor
    if is_directory:
        source.mkdir()
    else:
        source.write_text("", encoding="utf-8")
    calls: list[tuple[Path, Path, bool]] = []

    def fake_symlink_to(
        destination: Path,
        target: Path,
        *,
        target_is_directory: bool = False,
    ) -> None:
        calls.append((destination, target, target_is_directory))

    monkeypatch.setattr(Path, "symlink_to", fake_symlink_to)

    campaign._ensure_project_symlink(repo, workspace, anchor)

    assert calls == [(workspace / anchor, source, is_directory)]


def test_managed_symlink_rejects_foreign_and_regular_destinations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    foreign = tmp_path / "foreign"
    (repo / "tests").mkdir(parents=True)
    workspace.mkdir()
    foreign.mkdir()

    destination = workspace / "tests"
    destination.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="points outside the project"):
        campaign._ensure_project_symlink(repo, workspace, "tests")

    destination.unlink()
    destination.mkdir()
    with pytest.raises(ValueError, match="not a managed symlink"):
        campaign._ensure_project_symlink(repo, workspace, "tests")


def test_workspace_preserves_associations_for_identical_inputs(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "kenshi_agent").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "kenshi_agent" / "memory.py").write_text(
        "def recall(): return []\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        """
[tool.mutmut]
source_paths = ["src/kenshi_agent/"]
pytest_add_cli_args_test_selection = ["tests/"]
""".lstrip(),
        encoding="utf-8",
    )
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    workspace = prepare_batch_workspace(repo, batch)
    stats = workspace / "mutants" / "mutmut-stats.json"
    stats.parent.mkdir()
    stats.write_text('{"kept": true}\n', encoding="utf-8")

    prepare_batch_workspace(repo, batch)

    assert json.loads(stats.read_text(encoding="utf-8")) == {"kept": True}


def test_workspace_recovers_from_malformed_fingerprint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "kenshi_agent").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "kenshi_agent" / "memory.py").write_text(
        "def recall(): return []\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src/kenshi_agent/"]\n',
        encoding="utf-8",
    )
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    workspace = prepare_batch_workspace(repo, batch)
    fingerprint = workspace / "input-fingerprint.json"
    fingerprint.write_text("{", encoding="utf-8")
    stats = workspace / "mutants" / "mutmut-stats.json"
    stats.parent.mkdir()
    stats.write_text("{}\n", encoding="utf-8")

    prepare_batch_workspace(repo, batch)

    assert not stats.exists()
    assert len(json.loads(fingerprint.read_text(encoding="utf-8"))["digest"]) == 64


def test_mutmut_executable_prefers_the_current_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    adjacent = tmp_path / "mutmut"
    monkeypatch.setattr(campaign.sys, "executable", str(python))

    assert campaign._mutmut_executable() == "mutmut"
    adjacent.write_text("", encoding="utf-8")
    assert campaign._mutmut_executable() == str(adjacent)


def test_read_results_invokes_mutmut_and_scopes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout=(
                "  kenshi_agent.memory.x_recall__mutmut_1: killed\n"
                "  kenshi_agent.planning.x_plan__mutmut_1: survived\n"
            )
        )

    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    monkeypatch.setattr(campaign, "_mutmut_executable", lambda: "/venv/mutmut")
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )

    summary = campaign._read_batch_results(tmp_path, batch)

    assert summary == MutationSummary(
        counts={"killed": 1},
        actionable_mutants=(),
    )
    assert calls == [
        (
            (["/venv/mutmut", "results", "--all", "true"],),
            {
                "cwd": tmp_path,
                "check": True,
                "capture_output": True,
                "text": True,
            },
        )
    ]


def test_run_artifact_preserves_exact_batch_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_at = datetime(2026, 7, 27, 21, 45, 12, tzinfo=UTC)

    class FixedDatetime:
        @classmethod
        def now(cls, timezone: object) -> datetime:
            assert timezone is UTC
            return completed_at

    monkeypatch.setattr(campaign, "datetime", FixedDatetime)
    batch = MutationBatch(
        name="env.live",
        source_path="src/kenshi_agent/env/live.py",
        mutant_pattern="kenshi_agent.env.live.*",
    )
    summary = MutationSummary(
        counts={"killed": 2, "survived": 1},
        actionable_mutants=("kenshi_agent.env.live.x_step__mutmut_2",),
    )

    artifact = campaign._write_run_artifact(tmp_path, batch, summary)
    with pytest.raises(FileExistsError):
        campaign._write_run_artifact(tmp_path, batch, summary)

    assert artifact == (tmp_path / "runs" / "mutation" / "20260727T214512Z-env-live.json")
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "batch": "env.live",
        "completed_at": "2026-07-27T21:45:12+00:00",
        "source_path": "src/kenshi_agent/env/live.py",
        "mutant_pattern": "kenshi_agent.env.live.*",
        "counts": {"killed": 2, "survived": 1},
        "total": 3,
        "actionable_mutants": ["kenshi_agent.env.live.x_step__mutmut_2"],
    }


def test_run_artifacts_share_their_directory_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_at = iter(
        (
            datetime(2026, 7, 27, 21, 45, 12, tzinfo=UTC),
            datetime(2026, 7, 27, 21, 45, 13, tzinfo=UTC),
        )
    )

    class AdvancingDatetime:
        @classmethod
        def now(cls, timezone: object) -> datetime:
            assert timezone is UTC
            return next(completed_at)

    monkeypatch.setattr(campaign, "datetime", AdvancingDatetime)
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    summary = MutationSummary(counts={"killed": 1}, actionable_mutants=())

    first = campaign._write_run_artifact(tmp_path, batch, summary)
    second = campaign._write_run_artifact(tmp_path, batch, summary)

    assert first != second
    assert first.is_file()
    assert second.is_file()


def test_run_artifacts_use_explicit_exclusive_utf8_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[object, ...], dict[str, object]]] = []
    written: list[str] = []

    class RecordingHandle:
        def __enter__(self) -> RecordingHandle:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def write(self, content: str) -> int:
            written.append(content)
            return len(content)

    def recording_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> RecordingHandle:
        calls.append((path, args, kwargs))
        return RecordingHandle()

    monkeypatch.setattr(Path, "open", recording_open)
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    summary = MutationSummary(counts={"killed": 1}, actionable_mutants=())

    artifact = campaign._write_run_artifact(tmp_path, batch, summary)

    assert calls == [(artifact, ("x",), {"encoding": "utf-8"})]
    assert json.loads(written[0])["counts"] == {"killed": 1}


def test_summary_prints_counts_and_each_actionable_mutant(
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    summary = MutationSummary(
        counts={"killed": 4, "timeout": 1},
        actionable_mutants=("kenshi_agent.memory.x_recall__mutmut_5",),
    )

    campaign._print_summary(batch, summary)

    assert capsys.readouterr().out == (
        "memory: total=5 killed=4 timeout=1\n  attend: kenshi_agent.memory.x_recall__mutmut_5\n"
    )


def test_empty_summary_has_no_trailing_whitespace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = MutationBatch(
        name="empty",
        source_path="src/kenshi_agent/empty.py",
        mutant_pattern="kenshi_agent.empty.*",
    )

    campaign._print_summary(
        batch,
        MutationSummary(counts={}, actionable_mutants=()),
    )

    assert capsys.readouterr().out == "empty: total=0\n"


def test_repo_root_is_derived_from_the_installed_module() -> None:
    assert campaign._repo_root() == Path(campaign.__file__).resolve().parents[2]


def test_main_lists_every_batch_and_rejects_unknown_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "src" / "kenshi_agent"
    source_root.mkdir(parents=True)
    (source_root / "memory.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(campaign, "_repo_root", lambda: tmp_path)

    assert campaign.main(["list"]) == 0
    assert capsys.readouterr().out == ("memory\tsrc/kenshi_agent/memory.py\n")
    with pytest.raises(SystemExit) as exc_info:
        campaign.main(["results", "missing"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("command", "allow_actionable", "expected"),
    [
        ("results", False, 1),
        ("results", True, 0),
        ("run", False, 1),
        ("run", True, 0),
    ],
)
def test_main_gates_actionable_results_for_run_and_results_commands(
    command: str,
    allow_actionable: bool,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    calls: list[object] = []
    dependency_calls: list[object] = []
    monkeypatch.setattr(campaign, "_repo_root", lambda: tmp_path)

    def fake_discover(source_root: Path) -> dict[str, MutationBatch]:
        dependency_calls.append(("discover", source_root))
        return {"memory": batch}

    monkeypatch.setattr(
        campaign,
        "discover_mutation_batches",
        fake_discover,
    )

    def fake_prepare(
        repo_root: Path,
        selected: MutationBatch,
    ) -> Path:
        dependency_calls.append(("prepare", repo_root, selected))
        return tmp_path / "workspace"

    monkeypatch.setattr(
        campaign,
        "prepare_batch_workspace",
        fake_prepare,
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    summary = MutationSummary(
        counts={"survived": 1},
        actionable_mutants=("kenshi_agent.memory.x_recall__mutmut_1",),
    )

    def fake_read(
        workspace: Path,
        selected: MutationBatch,
    ) -> MutationSummary:
        dependency_calls.append(("read", workspace, selected))
        return summary

    def fake_write(
        repo_root: Path,
        selected: MutationBatch,
        selected_summary: MutationSummary,
    ) -> Path:
        dependency_calls.append(("write", repo_root, selected, selected_summary))
        return tmp_path / "runs" / "result.json"

    def fake_print(
        selected: MutationBatch,
        selected_summary: MutationSummary,
    ) -> None:
        dependency_calls.append(("print", selected, selected_summary))

    monkeypatch.setattr(campaign, "_read_batch_results", fake_read)
    monkeypatch.setattr(campaign, "_write_run_artifact", fake_write)
    monkeypatch.setattr(campaign, "_print_summary", fake_print)
    arguments = [command, "memory"]
    if allow_actionable:
        arguments.append("--allow-actionable")

    assert campaign.main(arguments) == expected
    if command == "run":
        assert calls == [
            (
                (
                    [
                        campaign._mutmut_executable(),
                        "run",
                        "--max-children",
                        "6",
                        "kenshi_agent.memory.*",
                    ],
                ),
                {"cwd": tmp_path / "workspace", "check": False},
            )
        ]
    else:
        assert calls == []
    assert capsys.readouterr().out == "artifact: runs/result.json\n"
    assert dependency_calls == [
        ("discover", tmp_path / "src" / "kenshi_agent"),
        ("prepare", tmp_path, batch),
        ("read", tmp_path / "workspace", batch),
        ("write", tmp_path, batch, summary),
        ("print", batch, summary),
    ]


def test_main_propagates_mutmut_failure_before_reading_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    monkeypatch.setattr(campaign, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        campaign,
        "discover_mutation_batches",
        lambda source_root: {"memory": batch},
    )
    monkeypatch.setattr(
        campaign,
        "prepare_batch_workspace",
        lambda repo_root, selected: tmp_path / "workspace",
    )
    monkeypatch.setattr(
        campaign.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )
    monkeypatch.setattr(
        campaign,
        "_read_batch_results",
        lambda *args: pytest.fail("results read after mutmut failed"),
    )

    assert campaign.main(["run", "memory", "--max-children", "3"]) == 7


def test_main_rejects_nonpositive_worker_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    monkeypatch.setattr(campaign, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        campaign,
        "discover_mutation_batches",
        lambda source_root: {"memory": batch},
    )
    monkeypatch.setattr(
        campaign,
        "prepare_batch_workspace",
        lambda repo_root, selected: tmp_path / "workspace",
    )

    with pytest.raises(SystemExit) as exc_info:
        campaign.main(["run", "memory", "--max-children", "0"])

    assert exc_info.value.code == 2


def test_main_accepts_one_mutation_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = MutationBatch(
        name="memory",
        source_path="src/kenshi_agent/memory.py",
        mutant_pattern="kenshi_agent.memory.*",
    )
    summary = MutationSummary(counts={"killed": 1}, actionable_mutants=())
    run_calls: list[object] = []
    monkeypatch.setattr(campaign, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        campaign,
        "discover_mutation_batches",
        lambda source_root: {"memory": batch},
    )
    monkeypatch.setattr(
        campaign,
        "prepare_batch_workspace",
        lambda repo_root, selected: tmp_path / "workspace",
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        run_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    monkeypatch.setattr(
        campaign,
        "_read_batch_results",
        lambda workspace, selected: summary,
    )
    monkeypatch.setattr(
        campaign,
        "_write_run_artifact",
        lambda repo_root, selected, result: tmp_path / "result.json",
    )
    monkeypatch.setattr(campaign, "_print_summary", lambda selected, result: None)

    assert campaign.main(["run", "memory", "--max-children", "1"]) == 0
    assert run_calls[0][0][0][3] == "1"
