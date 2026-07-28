# Kenshi Agent Environment

A safety-first agent environment for Kenshi: versioned native telemetry,
screenshots, persistent memory, structured continuous planning, semantic action
contracts, analyzable lifecycle logs, and explicitly labelled control modes.

`interface_only` is the default and uses ordinary Windows input.
`native_assisted` additionally permits a small set of reviewed native command
bridges, and is never merged silently into interface-only evidence.

Today the project supports a supervised, open-ended but bounded town-local,
single-character live loop—not broad Kenshi competence. Its long-term direction
is a reusable agent substrate with causally verified game-specific affordance
adapters. The [platform ADR](docs/ADR_GAME_AFFORDANCE_PLATFORM.md) records that
direction without changing current Kenshi scope.

## Where things are written down

| Question | Source |
| --- | --- |
| What works right now, and what does not | [STATUS.md](STATUS.md) |
| What the agent may author | [generated action catalog](docs/generated/ACTION_CATALOG.md) |
| Which modules are mutation-tested against *this* tree | [generated attestation](docs/generated/MUTATION_ATTESTATION.md), and [why it is derived](docs/ADR_MUTATION_ATTESTATION.md) |
| Why a boundary exists | `docs/ADR_*.md` |
| How to do something | `docs/GUIDE_*.md` |
| What happened on a given day | `git log` and `runs/<run-id>/` |

Documentation hygiene is enforced by `tests/test_docs_hygiene.py` rather than by convention:
docs are capped at 120 lines, must be a decision record, a guide, or generated output, and
generated files fail the build when they go stale.

## Five-minute mock run

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
kenshi-agent doctor --config config/default.yaml
pytest
kenshi-agent run --config config/default.yaml --mode mock --planner heuristic --steps 40
```

The command prints a run directory whose `events.jsonl` records observations,
world-state updates, decisions, receipts, memory writes, and termination.
Summarize it with `kenshi-agent summarize runs\<RUN_ID>\events.jsonl`.
Rank typed capability gaps across runs with `kenshi-agent aggregate-affordances runs`.

Planners available: heuristic, scripted, external subprocess (see
[`GUIDE_EXTERNAL_PLANNER_PROTOCOL.md`](docs/GUIDE_EXTERNAL_PLANNER_PROTOCOL.md)),
OpenAI Responses, and OpenRouter. Note that the default compact observation
digests are not accepted by `ReplayEnvironment`; set
`runtime.log_full_observations: true` when full replay is needed.

## Repository map

```text
config/            Mock and live configuration
prompts/           Planner and memory prompts
schemas/           Generated JSON Schemas
src/kenshi_agent/  Python environment and agent runtime
native/            KenshiLib telemetry and reviewed command bridge
benchmarks/        Experiment definitions
examples/          Sample telemetry and scripted policy
docs/              ADRs, guides, and generated references
scripts/           Bootstrap, test, run, and staging helpers
tests/             Platform-independent automated tests
runs/              Local screenshots, logs, outputs; gitignored
```

## Going live

Read [`GUIDE_LIVE_RUNS.md`](docs/GUIDE_LIVE_RUNS.md) first. Rebuilding the native
plug-in is covered by
[`GUIDE_WINDOWS_NATIVE_SETUP.md`](docs/GUIDE_WINDOWS_NATIVE_SETUP.md); pinned
toolchain and upstream versions are in
[`GUIDE_UPSTREAM_LOCK.md`](docs/GUIDE_UPSTREAM_LOCK.md).

Use a disposable save. Close anything containing secrets before running a vision
model that receives desktop screenshots.

## Safety model

`interface_only` filters out native control capabilities and rejects
native-assisted skills twice. `native_assisted` permits only the marked reviewed
command bridges — never teleporting, stat/money/faction mutation, save/load, or
arbitrary game methods. Logs and summaries label the mode so evidence cannot be
conflated.

The Python guard enforces action-kind and skill allowlists, normalized click and
client-area bounds, per-skill pointer envelopes, bounded movement pulses with
telemetry-confirmed re-pause, polite input leases with idle detection and
foreground/cursor restoration, stale-telemetry click blocking, maximum wait
duration, macro expansion limits, per-minute rate limits, configuration plus CLI
live-input gates, and an emergency-stop key.

Rationale lives in the ADRs: [control modes](docs/ADR_CONTROL_MODES.md),
[world-state stream](docs/ADR_WORLD_STATE_STREAM.md), [stable native
identity](docs/ADR_STABLE_NATIVE_IDENTITY.md), [causal native
commands](docs/ADR_CAUSAL_NATIVE_COMMANDS.md), [safety
supervision](docs/ADR_SAFETY_SUPERVISOR.md), [stateful movement
options](docs/ADR_STATEFUL_MOVEMENT_OPTIONS.md), [strategic
advisor](docs/ADR_STRATEGIC_ADVISOR.md). Broader boundaries are in
[SECURITY_AND_SAFETY.md](SECURITY_AND_SAFETY.md).

## Experimental discipline

Report at least four failure categories separately: perception, planning/world model,
action compilation or interface control, and native telemetry or environment.

Run screenshot-only, screenshot-plus-telemetry, and telemetry-plus-skills conditions
separately. Do not optimize against one save and then present the result as general play
ability. `benchmarks/one_day_survival.yaml` is a starting point.

## License and project status

Research scaffolding under active development; interfaces, protocol versions,
and the action catalog change without notice. GPL-3.0-or-later, because the
native plug-in links against GPL-licensed KenshiLib. Kenshi is owned by Lo-Fi
Games; this project is unofficial and contains no game assets or binaries.
