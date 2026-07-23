# Kenshi Agent Environment

A safety-first scaffold for turning Kenshi into an agentic environment with a
versioned observation protocol, screenshots, persistent memory, structured
planning, bounded skills, replayable logs, and explicitly labeled control
modes. The default `interface_only` mode uses ordinary Windows input. The
optional `native_assisted` mode may also use narrowly reviewed internal command
bridges and is never merged silently into interface-only evidence.

This is not a claim that an LLM can already play Kenshi well. It is the machinery
needed to run that experiment without confusing perception, planning, input,
and game-integration failures.

## What is runnable now

- A deterministic Kenshi-like mock environment with the same `reset`, `observe`,
  `step`, and `close` interface used by live mode.
- Strict action, telemetry, observation, single-step decision, bounded plan,
  plan-patch, receipt, and memory schemas.
- A heuristic baseline that can complete the bundled one-day survival mock
  benchmark.
- Scripted and subprocess planner adapters.
- An optional OpenAI vision planner using screenshot plus structured telemetry.
- SQLite autobiographical memory and append-only JSONL run logs.
- Replay summaries and JSON Schema export.
- Feature-flagged continuous planning for mock/fake environments: one strategic
  call can authorize a bounded, revision-checked multi-action plan while the
  executor owns branches, retries, budgets, cancellation, and verification.
- A bounded in-process world-state stream for continuous mode: one observation
  pump feeds validated revisions, state deltas, transient events, entity
  lifetimes, subscribers, and command/plan causality without each consumer
  polling the environment.
- A Windows client-area screenshot and SendInput controller.
- Two independent gates before real keyboard or mouse input is allowed.
- Typed `interface_only` and `native_assisted` control modes, with an additional
  acknowledgement before native-assisted live execution.
- Native KenshiLib plugin source that emits partial telemetry through an atomic
  JSON file and, only when explicitly enabled by the Python control mode, exposes
  one bounded vendor-approach command bridge.
- Automated tests for the platform-independent path.

The native plugin compiles as a VS2010 SP1 `Release | x64` DLL against the
pinned maintained KenshiLib dependency bundle. Its initial in-game smoke test
now passes with RE_Kenshi 0.3.4/KenshiLib 0.4.0: the hook reaches `ready`, emits
schema-valid snapshots at two hertz, and tracks one-character selection,
position, movement, pause, speed, and money. Broader field and input validation
is still incomplete. See `STATUS.md` and `docs/LIVE_VALIDATION_CHECKLIST.md`.

## Repository map

```text
config/                  Mock and live configuration
prompts/                 Planner and memory prompts
schemas/                 Generated JSON Schemas
src/kenshi_agent/        Python environment and agent runtime
native/KenshiAgentTelemetry/
                         KenshiLib telemetry and reviewed command bridge
benchmarks/              Experiment definitions
examples/                Sample telemetry and scripted policy
docs/                    Protocol and validation notes
scripts/                 Bootstrap, test, run, and staging helpers
tests/                   Platform-independent automated tests
runs/                    Local screenshots, logs, and outputs; gitignored
kenshi-agent-env-continuous-agent-loop-prompt.md
                         Loopable continuous-agent engineering brief
```

## Five-minute mock run

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
kenshi-agent doctor --config config/default.yaml
pytest
kenshi-agent run --config config/default.yaml --mode mock --planner heuristic --steps 40
```

The command prints a run directory. Its `events.jsonl` records observations,
world-state updates/events, decisions, action receipts, memory writes, and
termination events. Mock screenshots are saved under that run.

Summarize a run with:

```powershell
kenshi-agent summarize runs\<RUN_ID>\events.jsonl
```

### Continuous mock proof

`single_step` remains the default regression path. To exercise the first
continuous-planning slice without changing YAML:

```powershell
kenshi-agent run `
  --config config/default.yaml `
  --mode mock `
  --planner heuristic `
  --planning-mode continuous `
  --steps 2
```

The heuristic returns one two-step `PlanEnvelope` (`pause=false`, then
`speed=3`). Before each action, deterministic code rechecks the plan assumptions,
capabilities, typed preconditions, control mode, and remaining budgets, then
uses the same `ActionGuard` and environment path as `single_step`. A
postcondition counts only on a later telemetry revision. Changed, unknown,
unavailable, or stale preconditions cancel the future action instead of being
treated as false or silently repaired.

One independently owned observation pump supplies telemetry-only updates unless
a consumer explicitly requests a new visual frame. Its bounded store preserves
the latest validated snapshot, the last valid visual frame, deltas, transient
events, nearby-entity lifetimes, active plan/step/command state, and isolated
subscriber queues. Command receipts carry their command ID plus start and
canonical completion revisions; an unchanged revision is logged as
inconclusive rather than progress.

Continuous mode is intentionally restricted to mock and fake event-driven
environments in this slice. It is not a claim of live Kenshi continuity.
The complete executor and condition contract is recorded in
[Continuous planning](docs/CONTINUOUS_PLANNING.md).

## Planner options

### Heuristic baseline

The baseline is intentionally simple and inspectable. It establishes whether the
environment works independently of model behavior.

```powershell
kenshi-agent run --config config/default.yaml --planner heuristic --steps 40
```

### Scripted policy

In `single_step`, each non-comment line is one complete `PlannerDecision` JSON
object. Continuous scripts may contain a `PlanEnvelope`; `PlanPatch` parsing is
versioned now, while active-plan patch application is deferred.

```powershell
kenshi-agent run `
  --config config/default.yaml `
  --planner scripted `
  --script examples/scripted_policy.jsonl `
  --steps 10
```

### External subprocess

The runtime writes one `Observation` JSON line to the child process's stdin. The
child must write one `PlannerDecision` JSON object in `single_step`, or one
`PlanEnvelope` in `continuous`, to stdout and exit zero. This is the cleanest
connector for a coding-agent harness, local model, or custom orchestrator.

```powershell
kenshi-agent run `
  --config config/default.yaml `
  --planner subprocess `
  --command "python scripts/external_planner_example.py" `
  --steps 20
```

See `docs/EXTERNAL_PLANNER_PROTOCOL.md`.

### Hosted vision planners

Create an API key in the [OpenAI dashboard](https://platform.openai.com/api-keys),
copy the ignored environment template, add the key locally, and install the
optional dependency:

```powershell
Copy-Item .env.example .env
# Edit .env so it contains OPENAI_API_KEY=your-key-here
.\scripts\bootstrap_live_windows.ps1 -WithOpenAI
.\scripts\run_live_dry.ps1 `
  -Config config\live.example.yaml `
  -Planner openai `
  -Steps 1
```

The CLI loads only `.env` in its current working directory. Existing process
environment variables take precedence, and key values are never printed by the
doctor. The PowerShell entrypoints set the working directory to the repo root.
The active profile defaults to `gpt-5.6-luna` with low reasoning effort. Luna
keeps image input and structured decisions while targeting lower latency and
cost than Terra. Set `KENSHI_AGENT_MODEL` in `.env` to override it.
Set `KENSHI_AGENT_REASONING_EFFORT` to compare `none`, `low`, or a higher
model-supported effort without editing the profile.

OpenRouter is also supported through its OpenAI-compatible Chat API. Add
`OPENROUTER_API_KEY` to `.env`, select `--planner openrouter`, and optionally set
`KENSHI_AGENT_OPENROUTER_MODEL`. The default is `openai/gpt-5.6-luna`; provider
routing is sorted by latency and requires structured-output support. Override
the sort with `KENSHI_AGENT_OPENROUTER_SORT=throughput` or `price`.

The planner receives a bounded JSON observation, including planning mode,
control mode, exact world revision, and only the skills legal in that mode,
plus a base64 image when enabled. It returns a validated `PlannerDecision` in
`single_step` or a bounded `PlanEnvelope` in `continuous`; it does not call input
APIs itself.

### Live decision overlay

The active profile prints a human-readable stream for every turn: control mode,
planning latency, intent, concise rationale, action, confidence, and execution result.
It also records planner latency in `events.jsonl`, and `kenshi-agent summarize`
reports mean, median, and p95 latency for new runs.

On Windows, the overlay launcher puts the same feed in a translucent,
always-on-top window over the game:

```powershell
.\scripts\run_live_overlay.ps1 `
  -Planner openai `
  -Steps 30 `
  -ExecuteLiveActions `
  -AcknowledgeNativeAssistedControl
```

Use `-Planner openrouter` after adding `OPENROUTER_API_KEY`. The viewer is an
external read-only process that follows the append-only run log; it never calls
Kenshi UI code or input APIs. Windows capture exclusion keeps the viewer out of
the screenshots sent to the model; if that OS call fails, the viewer closes
itself rather than contaminate model input. It stays open for 30 seconds after a
run by default, and can be closed normally at any time.

## Moving toward live Kenshi

Do these in order. Skipping the order makes failures hard to diagnose.

1. Run the full mock tests and preserve a green baseline.
2. Build and install the native plugin against the exact maintained
   RE_Kenshi/KenshiLib versions used by the game.
3. Confirm `plugin_status.json` and a steadily increasing telemetry sequence.
4. Prepare the isolated Windows live runtime, then run `doctor` and
   `validate-telemetry` against live output.
5. Run live mode with action execution disabled. Inspect screenshots, telemetry,
   prompts, and proposed decisions.
6. Fix the resolution, window mode, UI scale, and key bindings; calibrate any
   semantic UI anchors.
7. Enable one harmless key skill at a time on a disposable save.
8. Only after those checks, enable model-selected live actions.

When the checkout lives in WSL, keep the live Python process and SQLite memory
database on Windows. From Windows PowerShell in the repo, run:

```powershell
.\scripts\bootstrap_live_windows.ps1
.\scripts\run_live_dry.ps1 -Config config\live.example.yaml -Steps 4
```

Add `-WithOpenAI` to the bootstrap command only when preparing to test the
vision planner. The dry-run command deliberately omits the second live-action
gate, so proposed actions are logged but not sent to Kenshi.

For the active burn-in, use the dedicated profile. It enables live input but
allows only pause, wait, map, calibrated map zoom, inventory, close-overlay,
focus-selected, bounded movement/person-interaction, and the one guarded
purchase flow. Raw keys and clicks, combat, unbounded purchasing, and save
operations remain blocked:

```powershell
kenshi-agent run `
  --config config/live.burnin.yaml `
  --planner openai `
  --execute-live-actions `
  --acknowledge-native-assisted-control
```

The burn-in profile is explicitly `native_assisted` because
`approach_confirmed_vendor` asks the plugin to issue Kenshi's internal
`PLAYER_TALK_TO` order. The dedicated CLI acknowledgement is required in
addition to the normal live-input gates. Use `config/live.example.yaml` for the
default `interface_only` experiment; that mode omits native control capabilities
and skills from observations and rejects a native-assisted skill even if it is
submitted manually.

The active profile defaults to 30 planner decisions and the lower-latency Luna
planner. Fine and coarse movement run as executor-controlled pulses: while
paused, the model chooses both a destination and a bounded duration. Fine pulses
may be 0.35–3.0 seconds and coarse map pulses 1.0–8.0 seconds. Fresh telemetry
must confirm re-pause before another model call, and direct model-selected
unpause is blocked.

Live capture and action execution also use a polite input lease. The controller
waits for 1.25 seconds without keyboard or mouse activity, remembers the current
foreground window and cursor, and briefly focuses Kenshi. After an action it
Alt+Tabs back to the previous context before restoring the cursor, preventing
Kenshi edge-scroll from reacting to off-screen or secondary-monitor coordinates.
If human input resumes during movement, the executor
ends the pulse, guarantees re-pause, yields control, and later makes a fresh
plan rather than retrying the stale intent. These timings and restoration
behaviors are configurable under `controls`.

`config/live.example.yaml` derives telemetry and SQLite paths from Windows
`%LOCALAPPDATA%`; copy it only when you need machine-specific overrides. Live
mode remains dry-run unless both conditions are true:

```yaml
safety:
  live_actions_enabled: true
```

and:

```powershell
kenshi-agent run --config config/my-live.yaml --mode live --execute-live-actions
```

Native-assisted execution additionally requires:

```yaml
control:
  mode: native_assisted
  native_assisted_actions_enabled: true
```

and `--acknowledge-native-assisted-control`.

F12 is the default emergency-stop key and is checked before each primitive input.
The Kenshi process and controller should run at the same Windows integrity level.
Fine world movement and coarse map travel use separate right-click skills with
different calibrated envelopes; see [Movement skills](docs/MOVEMENT_SKILLS.md).
The next supervised vertical slice is a guarded one-item purchase; its staged
state machine and spending policy are recorded in
[Food procurement](docs/FOOD_PROCUREMENT.md).
The installed keymap audit and open-source plugin/API survey are recorded in
[Kenshi control and plugin research](docs/KENSHI_CONTROL_AND_PLUGIN_RESEARCH.md).
The live-validated close follow-camera setup is recorded in
[Camera view for agent runs](docs/CAMERA_VIEW.md).
Short-horizon continuity and no-op feedback are recorded in the
[Action continuity ledger](docs/ACTION_LEDGER.md).

### Live development console

From WSL, the checked-in `./dev` wrapper locates the isolated Windows runtime
and provides short commands for the operations used during live iteration:

```bash
./dev launch
./dev shot --label bar-entrance
./dev telemetry
./dev journey --objective "Locate the visible bar entrance" --steps 8
./dev journey --objective "Approach the Barman safely" --steps 8 --execute \
  --exclusive --native-assisted
```

`launch` opens the RE_Kenshi shortcut, advances the video launcher, continues
the current save, and returns with fresh telemetry and the game paused. Journey
objectives override the YAML profile for one run only. Live input still requires
the explicit `--execute` gate; native-assisted execution additionally requires
`--native-assisted`. `--exclusive` keeps Kenshi in the foreground only when the
human has handed the session to the agent.

## Native telemetry bridge

The plugin hooks `PlayerInterface::update`, calls the original update first, and
samples the game/UI thread at two hertz. Its telemetry path is observational. It
currently exports:

- loaded, paused, speed, and money;
- camera position and center;
- basic squad identity, selection, life/consciousness state, position, movement
  speed, and food-item count;
- bounded nearby-character identity, role flags, faction/disposition evidence,
  world positions, viewport visibility, and normalized screen positions;
- current world, inventory, dialogue, and trade screen classification.

It intentionally does not pretend to export fields that have not been validated:
hunger, wound detail, getting-eaten state, generic inventory grids, dialogue
option text, context menus, current tasks, and broader faction interpretation
remain work items.

The same DLL also contains a separately labeled native-assisted vendor-approach
bridge. Python removes its capability and acknowledgement state in
`interface_only`; `ActionGuard` and `LiveEnvironment` independently reject its
skill in that mode.

Build instructions and the manual verification sequence are in
[the native plugin README](native/KenshiAgentTelemetry/README.md). Contributors
provisioning the legacy Windows compiler and pinned dependency bundle should
start with the [Windows native setup guide](docs/WINDOWS_NATIVE_SETUP.md); exact
media identities are recorded without redistributing proprietary installers.
The full coding-agent brief explains how to expand telemetry one field at a time
without turning reverse-engineered assumptions into a fragile world dump.

## Telemetry design

Snapshots are complete JSON documents atomically replaced at a known path. Each
snapshot contains:

- a semantic protocol version;
- a monotonically increasing sequence;
- a UTC capture timestamp;
- an explicit capability list;
- partial game, camera, UI, squad, and visible-entity state;
- warnings for known omissions or degraded sampling.

Missing data means unknown, not zero. The planner must only trust fields listed
by capabilities and must stop or pause when live telemetry becomes stale.

Generate or refresh schemas with:

```powershell
kenshi-agent export-schemas --output schemas
```

See `docs/TELEMETRY_PROTOCOL.md`.

## Safety model

`interface_only` is the default experiment boundary: player actions go through
visible keyboard and mouse input, native control capabilities are filtered out,
and native-assisted skills are rejected twice. `native_assisted` permits only
the specifically marked and reviewed internal command bridges; it does not
permit teleporting, stat/money/faction mutation, save/load, or arbitrary game
methods. Run logs and summaries label the mode so evidence cannot be conflated.
The rationale and enforcement points are recorded in
[ADR: Explicit control modes](docs/ADR_CONTROL_MODES.md).
Continuous-mode revision ownership and its current identity limits are recorded
in [ADR: Authoritative world-state stream](docs/ADR_WORLD_STATE_STREAM.md).

The Python guard enforces:

- action-kind and skill allowlists;
- normalized click bounds and client-area bounds when known;
- per-skill normalized pointer envelopes for calibrated movement macros;
- model-selected bounded movement pulses with telemetry-confirmed re-pause;
- polite input leases with idle detection and foreground/cursor restoration;
- stale-telemetry click blocking;
- maximum wait duration;
- macro expansion limits;
- per-minute primitive-action rate limits;
- configuration plus CLI live-input gates;
- an emergency-stop key.

Use a disposable save. Close applications containing secrets before testing a
vision model that receives desktop screenshots.

## Experimental discipline

Always report at least four failure categories separately:

1. observation/perception failure;
2. planning or world-model failure;
3. action compilation or interface-control failure;
4. native telemetry or environment failure.

The bundled benchmark specification in `benchmarks/one_day_survival.yaml` is a
starting point. Run screenshot-only, screenshot-plus-telemetry, and
telemetry-plus-skills conditions separately. Do not optimize against one save and
then present the result as general play ability.

## Known limitations

- Existing native build/load and supervised Kenshi evidence is version-specific
  and predates explicit run-level control-mode labels; it is not a generic
  compatibility claim.
- Native stable entity handles and medical detail are not yet exported.
- UI skills beyond ordinary configurable key macros require calibration and
  screenshot-grounded confirmation.
- Hosted vision-planner evidence is limited to supervised narrow live slices.
- Continuous execution currently has no independent safety supervisor,
  planner/executor overlap, live movement option, or active patch application.
- SendInput can fail when Windows integrity levels differ or foreground focus is
  denied.
- The mock world tests orchestration, not Kenshi strategy competence.

## License and project status

The repository is GPL-3.0-or-later because its native plugin is designed to link
against GPL-licensed KenshiLib. Kenshi is owned by Lo-Fi Games. This project is
unofficial and includes no game assets or binaries.
