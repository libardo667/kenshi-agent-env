# Kenshi Agent Environment

A bounded agent runtime that plays Kenshi through current telemetry, screenshots,
runtime-generated affordances, ordinary Windows input, and a small reviewed native
command bridge.

The playing model does not write key sequences, click coordinates, native calls, or
retry loops. It chooses one exact affordance offered from the current observation;
the runtime rebinds that choice, authorizes it against fresh evidence, performs it
through one operation handler, and records one causal outcome.

This is experimental, supervised software—not a claim of broad Kenshi competence.
The proven live surface includes squad selection and movement, dialogue, trade,
bounded resource harvesting, inventory ownership, human handoff, emergency stop,
and telemetry-confirmed final pause. Navigation and strategic play remain limited
by what the game currently exposes and what has been accepted live.

## Current architecture

The repository completed a staged architectural reconstruction on 2026-08-04. Its
surviving ownership model is deliberately small:

- Affordance adapters decide what the playing model may choose now.
- Operation definitions own prerequisites, risk, identity, and terminal contracts.
- One handler owns the mechanics of each private operation.
- `OperationAuthority` revalidates exact current authority at the input boundary.
- `RunCoordinator` owns observe → plan → execute → record sequencing.
- Independent owners handle safety preemption, human control, outcomes, continuity,
  and final safe state.
- Live, mock, and replay environments meet the same execution boundary through
  different external adapters.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the implemented design. The completed
[reconstruction plan](docs/ARCHITECTURE_RECONSTRUCTION.md) explains the ownership
changes and their stage gates; the
[Stage 8 acceptance report](docs/reconstruction/stage_8_acceptance.md) records the
portable, native, and supervised-live evidence.

## Portable quick start

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are recommended.

```bash
uv sync --extra dev
uv run kenshi-agent doctor --config config/default.yaml
uv run kenshi-agent run --config config/default.yaml --mode mock --steps 8
```

The default configuration uses the deterministic mock adapter and heuristic planner.
It does not send Windows or Kenshi input. Run evidence is written beneath `runs/`.

The principal portable checks are:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python scripts/export_schemas.py
uv run python scripts/export_docs.py
```

Generated files under `schemas/` and `docs/generated/` come from current models and
registries. Do not edit them by hand.

## Live Kenshi setup

Live operation currently targets Kenshi on Windows from the repository under WSL.
It expects:

- an owned Windows installation of Kenshi through Steam;
- RE_Kenshi and the `KenshiAgentTelemetry` native mod;
- Python 3.11 or newer on Windows for the input/capture process;
- the display, graphics, memory, and launcher prerequisites enforced by
  `config/live.yaml`; and
- an API key for the configured hosted planner in a local `.env` file.

Start with the checked-in environment template:

```bash
cp .env.example .env
```

From Windows PowerShell, install the editable live runtime used by `./dev`:

```powershell
.\scripts\bootstrap_live_windows.ps1 -WithOpenAI
```

Native build, conformance, staging, and installation instructions are in the
[plug-in README](native/KenshiAgentTelemetry/README.md). The native bridge is not an
arbitrary game API: it accepts a fixed protocol of reviewed, exact player-order
operations and publishes keyed causal acknowledgements.

Once the host and plug-in are prepared:

```bash
./dev scenario install-starts
./dev setup graphics
./dev doctor
./dev launch --title
```

`./dev doctor` is read-only. Launch refuses to continue when its exact Steam,
graphics, display, memory, telemetry, or start-state requirements are not proven.

## Running live

`./dev run` is the supported live entrypoint. It can use a verified authored start,
an immutable scenario fixture, or an already-loaded unambiguous world.

Begin with planning only:

```bash
./dev run \
  --game-start kae-03-broke-pair \
  --objective 'Assess the pair and make one grounded plan.' \
  --campaign first-pair-run \
  --steps 3 \
  --control plan-only
```

The control choices are literal:

- `plan-only` sends no gameplay actions.
- `polite-live` may act, then restores the prior foreground window and cursor.
- `exclusive-live` retains desktop input ownership for a deliberately handed-off
  session.

For an executing run, use a disposable save and choose `polite-live` or
`exclusive-live` explicitly. F12 is the emergency stop. Human input causes a visible
handoff; the independent supervisor can also preempt a run. If a run or terminal is
interrupted, use:

```bash
./dev recover
```

That command restores a safely paused state and releases stranded display ownership.
Use `./dev stop` to pause and close Kenshi through the supported path.

The complete generated command reference is
[docs/generated/DEV_CLI.md](docs/generated/DEV_CLI.md).

## Evidence and continuity

Each run has an append-only bundle under `runs/<run-id>/`. `events.jsonl` records
observations, planner delivery, affordance binding, operation lifecycle, receipts,
outcomes, preemption, and finalization. A receipt distinguishes acceptance, progress,
completion, cancellation, and failure; a sent input is not treated as proof that the
game changed.

Durable memory and fieldbook records are campaign-scoped in SQLite. Pass an explicit
`--campaign` for a save lineage that should retain continuity. A fresh campaign has no
authority to retrieve claims from older runs. The public `kenshi-agent memory` and
`kenshi-agent fieldbook` commands inspect those stores read-only.

Scenario labels are also evidence-bound. Tooling can restore and attest an immutable
fixture, while runtime validation checks the attestation against fresh telemetry. A
manual label alone is never promoted into a proven start state.

## Repository map

```text
src/kenshi_agent/core/       Dependency-leaf types and evidence vocabularies
src/kenshi_agent/execution/  Execution kernel and operation-handler families
src/kenshi_agent/env/        Live, mock, and replay environment adapters
src/kenshi_agent/tooling/    Development CLI, scenarios, audits, and exporters
native/                      KenshiLib telemetry and reviewed command bridge
config/                      Canonical mock and live policies
prompts/                     Playing-model and memory prompts
knowledge/                   Advisor corpus and imported reference material
scenarios/                   Authored starts and fixture metadata
schemas/                     Generated external schemas
docs/generated/              Generated operation and interface reports
tests/                       Portable behavior, authority, and fitness tests
runs/                        Local run evidence; ignored by git
```

## Scope and safety

The system preserves exact identity, telemetry freshness, input ownership, native
command causality, independent preemption, and final-state confirmation. Those are
epistemic guarantees: they constrain what the software may claim, not merely how it
is organized.

Live gameplay still has ordinary in-game consequences. Use disposable saves, keep
unrelated secret-bearing windows off the captured display, and remain available for
supervised acceptance runs. Mock evidence, replay evidence, and live persistent-world
evidence are intentionally kept distinct.

## License

GPL-3.0-or-later. The native plug-in links against GPL-licensed KenshiLib. Kenshi is
owned by Lo-Fi Games; this unofficial project contains no Kenshi game assets or game
binaries.
