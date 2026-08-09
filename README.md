# Kenshi Agent Environment

Kenshi Agent Environment lets a language model play a supervised game of Kenshi.

A native mod reads the game state and performs gameplay actions. The Python runtime
captures screenshots, builds a list of actions that are valid right now, asks the
model to choose from that list, checks the choice against fresh game state, and
records what happened.

The model does not write key presses, screen coordinates, native commands, or retry
loops. Gameplay actions offered to the model go through the native mod and do not use
mouse coordinates. Python currently sends a short hotkey after publishing most
native command requests; Windows input is also used to start and recover the game and
for host-safety operations.

This is experimental software for supervised runs with disposable saves. It is not
a general-purpose Kenshi bot.

## What works today

The current action set covers:

- selecting characters and issuing movement, regroup, building-exit, and map-travel
  orders;
- approaching people and issuing character orders that Kenshi itself reports as
  available;
- opening trade, looting, squadmate, and resource inventories;
- buying, selling, looting, collecting resource output, and moving items between
  open inventories through Kenshi's own inventory code;
- starting or adopting resource work and waiting for output;
- reading a local resource survey;
- pausing, changing game speed, waiting, and stopping a run;
- electively shifting into an eligible nearby body, including after losing the
  current party; and
- using campaign memory, the fieldbook, and the read-only strategy advisor.

The current 1.19 telemetry contract separately exports the complete player
`roster`, named `platoons` and exact membership, `active_platoon_id`,
`primary_character_id`, and the complete `selected_character_ids` set. The
primary is never inferred from roster order. The former `squad`, per-character
selection flag, and UI-owned selection fields are not compatibility aliases.
The named `player-topology-20260809T161112Z` live bundle proves two authored
nonempty platoons, tab and exact-selection changes, and save/load restoration
of membership, primary, and selection. Kenshi reset the active tab on load, so
the exporter reports active separately instead of claiming it persisted.

Live acceptance runs have covered representative movement, trade-window opening,
purchases, equipped-item looting, squadmate and resource-output transfers, resource
production, human handoff, emergency stop, and a confirmed final pause. Character
orders and body shifting were observed in supervised sessions, but no exact named
run bundle preserves either complete proof chain. The exact durable classification
for each operation is recorded in
[the proof ledger](docs/reconstruction/interaction_proof_status.json).

There are still important limits:

- The mod currently tracks only one active command. It cannot yet track separate
  commands for different groups at the same time.
- Some group behavior is still unproven, including group dialogue participation,
  mixed-building exits, threat-response scope, and delayed map-travel continuation
  after changing selection.
- Many controls visible in Kenshi are intentionally not offered to the model. The old
  pointer-based gameplay handlers were removed instead of being kept as a fallback.
- A native recovery command can close a trade window, but general window closing is
  not a planner action.

The remaining bounded-work and plural-command shape for the next breaking
boundary is in the
[Protocol 2.0 world-model decision](docs/PROTOCOL_2_WORLD_MODEL_DECISION.md).

## How a run works

1. The native mod publishes current Kenshi state, including roster and platoon
   topology, active platoon, primary and complete selection, nearby targets,
   inventories, dialogue, visible controls, and command results.
2. The runtime captures a matching screenshot and builds the actions available from
   that state.
3. The planner chooses one of those actions.
4. The runtime reads fresh state and refuses the action if its target, selection, or
   other requirements changed.
5. The native mod performs the action and reports whether Kenshi accepted it and what
   happened afterward.
6. The runtime writes the observation, decision, command, and result to the run log.
   A live run finishes by returning control and confirming that the game is paused.

Mock and replay runs use the same planner and operation path without controlling a
live game.

## Try the mock environment

CPython 3.11, 3.12, 3.13, and 3.14 are supported. The package metadata rejects
older and newer feature releases until they are covered by the portable matrix.
[uv](https://docs.astral.sh/uv/) is recommended.

```bash
uv sync --extra dev
uv run kenshi-agent doctor --config config/default.yaml
uv run kenshi-agent run --config config/default.yaml --mode mock --steps 8
```

The default config uses a seeded mock world and the built-in heuristic planner. It
does not send input to Windows or Kenshi. Run files are written under `runs/`.

## Set up live Kenshi

The supported live setup currently runs Kenshi on Windows from this repository under
WSL. It expects:

- Kenshi installed through Steam;
- RE_Kenshi and the `KenshiAgentTelemetry` native mod;
- CPython 3.11, 3.12, 3.13, or 3.14 on Windows for the live host process;
- the display, graphics, and memory setup checked by `config/live.yaml`; and
- an OpenRouter API key for the default live planner, or credentials for another
  configured planner.

Copy the environment template and add the credentials you use:

```bash
cp .env.example .env
```

From Windows PowerShell, install the editable live runtime:

```powershell
.\scripts\bootstrap_live_windows.ps1 -WithOpenAI
```

Build and install the native mod by following its
[setup instructions](native/KenshiAgentTelemetry/README.md). The mod is the main
gameplay control interface as well as the source of live telemetry.

Then prepare and check the host:

```bash
./dev scenario install-starts
./dev setup graphics
./dev doctor
./dev launch --title
```

`./dev doctor` only checks the system; it does not send input. `./dev launch` stops if
the required Steam login, graphics profile, display, memory, telemetry, or requested
start state cannot be confirmed.

## Run the agent live

`./dev run` is the normal live entrypoint. Start with a planning-only run:

```bash
./dev run \
  --game-start kae-03-broke-pair \
  --objective 'Assess the pair and make one grounded plan.' \
  --campaign first-pair-run \
  --steps 3 \
  --control plan-only
```

`plan-only` reads the game and calls the planner but sends no gameplay actions.

To let the agent act, use a disposable save and choose `live`:

```bash
./dev run \
  --game-start kae-03-broke-pair \
  --objective 'Find useful work and improve their situation.' \
  --campaign first-pair-run \
  --steps 12 \
  --control live
```

The live path asks for explicit confirmation before taking desktop control. Press
F12 for an emergency stop. Ordinary human input hands control back to the operator.

If a run or terminal is interrupted, use:

```bash
./dev recover
```

This pauses Kenshi and releases display ownership. Use `./dev stop` to pause and close
the game through the supported path.

For an interactive terminal launcher, run `./dev tui`. Other useful read-only tools
are:

```bash
./dev telemetry --watch
./dev affordances --watch
./dev snapshot --label before-test
```

See the [generated `./dev` command reference](docs/generated/DEV_CLI.md) for every
option.

## Run records and campaign memory

Every run gets a directory under `runs/<run-id>/`. Its `events.jsonl` file records
observations, planner choices, action binding, native commands, monitor state,
results, safety events, and finalization. Sending a command is not treated as proof
that the game changed; later telemetry supplies that proof when it is available.

Campaign memory and fieldbook entries are stored in SQLite. Pass `--campaign` when
runs belong to the same ongoing save. Runs without the same campaign name cannot read
each other's saved claims. The `kenshi-agent memory` and `kenshi-agent fieldbook`
commands inspect those records without changing them.

Scenario fixtures are also checked against current telemetry. Naming a scenario or
save is not enough by itself to claim that the expected world is loaded.

## Development checks

Run the complete portable gate from the repository root:

```bash
./dev verify-portable
```

The command installs the locked development dependencies, runs tests, Ruff, and
mypy, validates reverse-engineering evidence, regenerates schemas and documentation,
checks their bytes for staleness, and rejects whitespace errors. GitHub Actions runs
that same command on every supported Python version. Files under `schemas/` and
`docs/generated/` are generated from the current models and registries. Do not edit
them by hand.

Before adding controller-authored behavior for an inferred engine rule, start from
the [reverse-engineering evidence guide](game_sources/research/README.md) and the
[reverse-engineering issue form](.github/ISSUE_TEMPLATE/reverse-engineering-evidence.yml).
Each subsystem gets the same validated six-file package; the generated
[research index](docs/generated/RESEARCH_EVIDENCE_INDEX.md) shows the current
conclusions and withheld boundaries.

Useful current references:

- [operation definitions](docs/generated/OPERATION_DEFINITIONS.md)
- [interaction catalog](docs/generated/INTERACTION_CATALOG.md)
- [proof ledger](docs/reconstruction/interaction_proof_status.json)
- [native mod and protocol](native/KenshiAgentTelemetry/README.md)
- [Protocol 2.0 world-model decision](docs/PROTOCOL_2_WORLD_MODEL_DECISION.md)

The older [architecture reconstruction plan](docs/ARCHITECTURE_RECONSTRUCTION.md),
[interaction-scope plan](docs/archive/KENSHI_INTERACTION_SCOPE_ORDER_LIFECYCLE_PLAN.md),
[body-shift plan](docs/archive/KENSHI_BODY_SHIFT_PLAN.md), and
[Stage 8 acceptance report](docs/reconstruction/stage_8_acceptance.md) are historical
records, not current authority.

## Repository layout

```text
src/kenshi_agent/          Python runtime, planning, actions, and tools
native/                    Kenshi native mod and command protocol
game_sources/              Captured Kenshi declarations and research evidence
config/                    Mock and live configuration
prompts/                   Planner and memory prompts
knowledge/                 Strategy reference material
scenarios/                 Authored starts and saved fixtures
schemas/                   Generated data schemas
docs/generated/            Generated action and interface reports
tests/                     Portable tests
runs/                      Local run logs; ignored by Git
```

## Safety

Use disposable saves and stay present during live runs. Keep unrelated private
windows off the captured display. Mock runs, replay runs, and live Kenshi runs provide
different kinds of evidence and are not treated as interchangeable.

## License

GPL-3.0-or-later. The native mod links against GPL-licensed KenshiLib. Kenshi is owned
by Lo-Fi Games. This unofficial project does not include Kenshi game assets or game
binaries.
