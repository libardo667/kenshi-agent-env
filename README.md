# Kenshi Agent Environment

A safety-first agent environment for Kenshi with versioned native telemetry,
screenshots, persistent memory, structured continuous planning, semantic action
contracts, analyzable lifecycle logs, optional full-observation replay, and
explicitly labelled control modes. The default
`interface_only` mode uses ordinary Windows input. The optional
`native_assisted` mode also permits three narrowly reviewed movement command
bridges and is never merged silently into interface-only evidence.

The project now supports a supervised open-ended live loop over a bounded,
town-local, single-selected-character surface, not just a scaffold or a fixed
food-procurement demo. That is still not a claim of broad Kenshi competence:
the machinery is designed to keep perception, planning, input, native
integration, and safety failures attributable.

Documentation has explicit roles: [STATUS.md](STATUS.md) is current state,
[ARCHITECTURE.md](ARCHITECTURE.md) and ADRs hold enduring boundaries,
[the live checklist](docs/LIVE_VALIDATION_CHECKLIST.md) holds dated Windows and
in-game evidence, and
[the engineering ledger](docs/ENGINEERING_LOOP_STATE.md) is historical. Apply
[the documentation truth policy](docs/DOCUMENTATION_TRUTH.md) whenever a change
crosses code, native protocol, schemas, configuration, prompts, tests, mock
behavior, or public claims.

## What is runnable now

- A deterministic Kenshi-like mock environment and a 482-test portable
  regression suite.
- Strict schemas for telemetry, observations, decisions, bounded plans,
  future-only patches, actions, receipts, native requests, and memories.
- Heuristic, scripted, subprocess, OpenAI Responses, and OpenRouter planners.
- Single-step and bounded continuous schedulers. Continuous mode owns causal
  revisions, branches, retries, budgets, cancellation, postconditions, and
  concurrent future-plan advice.
- One authoritative observation pump, bounded world-state store, semantic
  old/new deltas, persistent plan memory, JSONL lifecycle logs, compact
  transcripts, lifecycle replay summaries, and evaluation metrics. The default
  compact observation digests are not accepted by `ReplayEnvironment`; set
  `runtime.log_full_observations: true` when full environment replay is needed.
- An independent deterministic safety supervisor and a final in-input-lease
  authorization fence.
- Ten declared reusable action contracts covering dialogue approach, local
  movement, visible controls, screen dismissal, buying, selling, equipping,
  game bindings, and scrolling. Targetless `move_in_direction` now has aligned
  Python/C++ request and acknowledgement models, keyed option ownership, shared
  cross-language fixtures, a native-build conformance gate, and one exact live
  completion proof. Protocol `0.6.1` survives the bounded stop-motion pause
  handoff and completes a direction after reaching its destination tolerance or
  crossing the intended destination plane.
- A Windows client-area capture and SendInput controller with polite handoff,
  explicit control ownership, F12, semantic current bounds, and calibration
  identity.
- Native protocol `0.6.1`, which emits stable identities, squad state and
  inventory, dialogue/trade/management UI, named item cells, combat state,
  camera facts, and a keyed acknowledgement ring for reviewed movement
  commands.

The native plugin compiles as a VS2010 SP1 `Release | x64` DLL against the
pinned KenshiLib bundle and has been exercised in supervised Kenshi 1.0.68
runs. See [current status](STATUS.md) for the exact supported surface and
[the live checklist](docs/LIVE_VALIDATION_CHECKLIST.md) for dated evidence and
remaining validation.

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

`single_step` remains the default regression path. Exercise the bounded
continuous scheduler without changing YAML:

```powershell
kenshi-agent run `
  --config config/default.yaml `
  --mode mock `
  --planner heuristic `
  --planning-mode continuous `
  --steps 2
```

The heuristic returns one two-step `PlanEnvelope`. Before each action,
deterministic code rechecks the plan assumptions, capabilities, typed
preconditions, control mode, and remaining budgets, then uses the same guard and
environment path as `single_step`. A postcondition counts only on a causally
later observation.

One observation pump feeds the bounded world-state store. The safety supervisor
subscribes independently, stateful options may overlap one future-only
`PlanPatch`, and every live step is revalidated after acquiring the input lease.
Changed, false, unknown, unavailable, or stale authority emits no input.

General continuous execution is available to mock/fake environments.
Live-continuous execution additionally requires an implemented policy and an
explicit CLI acknowledgement. The current generic policy is
`dialogue_interaction_v1`; despite its historical name, it validates every
planner-visible action through the same authoritative contract catalog and does
not prescribe a Barman or food sequence. See
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
object. Continuous scripts may contain a `PlanEnvelope`; when an observation
includes `active_plan`, a concurrent movement advisory may return a matching
future-only `PlanPatch`.

```powershell
kenshi-agent run `
  --config config/default.yaml `
  --planner scripted `
  --script examples/scripted_policy.jsonl `
  --steps 10
```

### External subprocess

The runtime writes one `Observation` JSON line to the child process's stdin. The
child must write one `PlannerDecision` JSON object in `single_step` or one
`PlanEnvelope` in `continuous` planning, then exit zero. This adapter does not
currently request `PlanPatch` during an active movement option; use a hosted
adapter for concurrent future-plan advice. The subprocess path remains the
cleanest connector for a coding-agent harness, local model, or custom
orchestrator.

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
The ordinary OpenAI profiles default to `gpt-5.6-luna`; the long-form profile
defaults to OpenRouter with `openai/gpt-4.1` and omits reasoning effort after a
five-run planner benchmark produced five valid plans at a 7.6-second median.
These are profile defaults, not compatibility requirements. Use
`KENSHI_AGENT_PLANNER`, `KENSHI_AGENT_MODEL`,
`KENSHI_AGENT_OPENROUTER_MODEL`, and `KENSHI_AGENT_REASONING_EFFORT` to run a
controlled comparison without editing YAML.

OpenAI Responses calls receive a mode-aware output ceiling. The configured base
allowance grows per expected plan/patch step and is capped independently of the
planner timeout. The condition path is a schema enum, so unsupported
abbreviations are rejected by structured generation rather than only by a later
executor validator.

OpenRouter is supported through its OpenAI-compatible Chat API. Add
`OPENROUTER_API_KEY` to `.env`, select `--planner openrouter`, and optionally set
`KENSHI_AGENT_OPENROUTER_MODEL`. Provider routing is sorted by latency and
requires structured-output support; override the sort with
`KENSHI_AGENT_OPENROUTER_SORT=throughput` or `price`. If a provider refuses the
compiled JSON Schema dialect, the adapter requests the same JSON shape in the
prompt and validates it locally rather than ending the run.

The planner receives a JSON observation with planning/control mode, world
revision, deltas, memories, deterministic dialogue/travel targets, current
semantic actions, window-grouped controls, advisor availability, and a base64
image when enabled.
The long-form profile also exposes a read-only strategic advisor. The playing
model may author one `consult_advisor` cognitive step when `advisor.may_request`
is true; a deterministic cadence/repetition signal sets `suggested` without
automatically spending a call. The consult emits zero controller primitives and
no world command. Its next observation contains a ranked, source-attributed
brief grounded in `knowledge/kenshi_strategy_v1.yaml`; the playing model still
has to verify current-world requirements and author the actual game plan.
`KENSHI_AGENT_ADVISOR_MODEL` selects the independently configured OpenRouter
model.
When `recover_camera_view` is advertised, the planner requests it with no
arguments and no success conditions. The controller alone binds the selected
character/HUD, establishes follow, searches bounded floors and fixed
zoom/orbit/tilt
candidates, scores retained frames, and returns `already_clear`, `recovered`,
or `failed_after_bounded_attempts`; the model does not author recovery
gestures.
Optional telemetry is reduced semantically to the configured spending budget.
The action/control envelope is preserved even when it exceeds that preference;
only the model's real context ceiling may truncate it, and truncation is stated
explicitly in the payload. The planner returns a validated `PlannerDecision`,
`PlanEnvelope`, or future-only `PlanPatch`; it never calls input APIs itself.

### Live decision overlay

The active profile prints a human-readable stream for decisions and continuous
plan lifecycle events: objective, steps, rejections, failures, safety
preemptions, planner latency, and execution results. `events.jsonl` retains the
typed machine-readable record used by summaries and evaluation metrics.
`transcript.log` is intended as the selectable copy of the feed, but some
current run paths do not create it; treat `events.jsonl` as the authoritative
retained artifact and verify the transcript exists before relying on it.

On Windows, the overlay launcher puts the same feed in a translucent,
always-on-top window over the game:

```powershell
.\scripts\run_live_overlay.ps1 `
  -Planner openai `
  -Steps 30 `
  -ExecuteLiveActions `
  -AcknowledgeNativeAssistedControl `
  -AcknowledgeContinuousLive
```

Use `-Planner openrouter` after adding `OPENROUTER_API_KEY`. The viewer is an
external read-only process that follows the append-only run log; it never calls
Kenshi UI code or input APIs. Windows capture exclusion keeps it out of model
screenshots and the window is click-through after it is mapped, so input reaches
the game. When present, use `transcript.log` when text must be selected, copied,
or searched.

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

For an open-ended supervised run, use the long-form profile. It runs the
continuous scheduler against the generic contracted action surface, leaves the
world running between actions, and preserves goals/commitments in SQLite
memory:

```powershell
kenshi-agent run `
  --config config/live.longform.yaml `
  --planner openrouter `
  --execute-live-actions `
  --acknowledge-native-assisted-control `
  --acknowledge-continuous-live `
  --exclusive-input-session
```

The profile is explicitly `native_assisted` because semantic approach and
movement use reviewed native pathing commands. It can approach and talk to any
current non-hostile dialogue target, move toward another nearby character,
operate visible controls, enter screens through Kenshi's own default bindings,
buy, sell, equip, scroll, and close windows. The declared bounded
`move_in_direction` action now keeps `target_id` empty by contract and binds
command identity to the selected character, bearing, and distance. Portable
tests and the native conformance executable pass, and the byte-identical 0.6.1
DLL is installed. Run `20260725T2223-direction-smoke-061-green` proved one exact
36.5-degree, 30-unit request from keyed acceptance through
`walk_destination_reached`, plausible movement, a changed resulting frame, and
safe final pause. Raw
controller primitives, save/load/editor bindings, arbitrary native tasks, and
direct game-state mutation remain unavailable.

`config/live.dialogue.yaml` is a shorter stop-motion continuous proof profile.
`config/live.burnin.yaml` is retained for legacy single-step calibrated runs;
its former food-specific continuous policy is retired and explicitly disabled.
Use `config/live.example.yaml` for the default `interface_only` experiment,
which strips native capabilities and acknowledgements and rejects
native-assisted actions even if submitted manually.

Live capture and execution use a polite input lease. Human input cancels the
active plan and hands control over; the long-form profile pauses for handback,
then restores a world that was running only after the quiet interval and
visible takeover countdown complete. Any new input resets that countdown and
F12 disarms automatic takeover. Every step is rebound and revalidated inside
the acquired lease against the newest canonical observation.

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
The retired calibrated movement and food flows remain documented as historical
evidence in [Movement skills](docs/MOVEMENT_SKILLS.md) and
[Food procurement](docs/FOOD_PROCUREMENT.md). The current action and telemetry
surface is summarized in [current status](STATUS.md) and measured in
[UI affordance coverage](docs/UI_AFFORDANCE_COVERAGE.md).
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
./dev graphics verify
./dev graphics apply
./dev launch --preflight-only
./dev launch
./dev shot --label bar-entrance
./dev telemetry
./dev journey --objective "Locate the visible bar entrance" --steps 8
./dev journey --config config/live.longform.yaml --continuous --execute \
  --native-assisted --acknowledge-continuous-live --exclusive --steps 30
```

`journey` flags are faithful passthroughs of `run` gates; the `run` command
still enforces each one. `--continuous` selects the continuous scheduler, and an
enabled continuous-live policy additionally requires
`--acknowledge-continuous-live` on top of `--execute` and `--native-assisted`.
`--continuous` alone never grants the acknowledgement.

The wrapper defaults to `config/live.burnin.yaml` for launch/graphics compatibility.
Pass `--config config/live.longform.yaml` on `journey` to select the current
open-ended policy; the later argument overrides the wrapper default.

`graphics verify` compares Kenshi's installed settings with the versioned
profile. `graphics apply` may run only while Kenshi is stopped; it makes a
timestamped backup, atomically installs the profile, and verifies the result.
Launch never silently changes graphics settings. `launch --preflight-only`
checks the Steam connection state, exact graphics profile, duplicate-client
guard, and configured physical-memory floor without starting Kenshi.

`launch` then backs up and disables RE_Kenshi's optional startup panel,
advances the native video dialog once with its default Enter action, and
selects configured title/save labels from bounded live MyGUI control telemetry.
It never retries focus-taking title clicks. New human input permanently cancels
the remaining startup sequence. Title startup is resolution-independent;
legacy gameplay pointer skills still require their exact calibration identity
until each has a semantic anchor. Success is delayed until the loaded squad
remains freshly observable and paused for the configured post-load health
window. Fresh native plug-in error state, an RE_Kenshi Crash Reporter,
`Kenshi has crashed`, `BAD STUFF`, or Steam DLL error window terminates the
launcher immediately without further input.
Journey objectives override the YAML profile for one run only. Live input still
requires the explicit `--execute` gate; native-assisted execution additionally
requires `--native-assisted`. `--exclusive` keeps Kenshi in the foreground only
when the human has handed the session to the agent. A profile with automatic
takeover enabled also opens a capture-excluded ownership window. Human input
cancels the active plan and yields control; after the configured quiet interval
it shows a resettable takeover countdown. Any new input resets that countdown
and F12 disarms automatic takeover for the run. A completed countdown creates a
fresh observation and plan rather than resuming cancelled work. Use
`--no-ownership-overlay` only when another viewer is following the same events.

## Native telemetry bridge

The plugin hooks Kenshi's title and loaded-game update points, calls the
original methods first, and samples on the game/UI thread at about two hertz.
Its telemetry path is observational. Protocol `0.6.1` currently exports:

- loaded, paused, speed, money, and elapsed in-game minutes;
- camera position/center and nearby-character camera bearings;
- stable squad identity and complete selection, life/conscious/down/crippled/
  combat state, position, movement, nutrition reserve, blood, and bounded named
  inventory/equipment facts;
- bounded nearby-character identity, role flags, faction/disposition evidence,
  world positions, viewport visibility, and normalized screen positions;
- world, inventory, dialogue, trade, stats, and management-window state;
- exact open-dialogue target and bounded option captions;
- current tooltip text/source bounds and up to 224 current buttons, named item
  cells, and text controls with window ownership and normalized bounds.

The current protocol retains validated session-scoped opaque IDs and a strict
atomic request plus bounded keyed acknowledgement ring. The same DLL contains
separately labelled native-assisted commands for approaching a valid dialogue
target, walking to an exact nearby character, and walking a bounded
bearing/distance. Python removes their capabilities and acknowledgement state
in `interface_only`; both the guard and environment reject them again. In
`native_assisted`, every request must match its command ID, current revision,
identity session, complete one-character selection, and exact target or bounded
direction fields. Old or different acknowledgements cannot certify execution.

Body-part wounds, bleeding rate, getting-eaten state, location name, current
tasks, geometry occlusion, distant world state, and broad faction mechanics
remain unavailable or unvalidated.

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
Native handle identity and lifecycle semantics are recorded in
[ADR: Stable native identity](docs/ADR_STABLE_NATIVE_IDENTITY.md).
Causal native request and acknowledgement semantics are recorded in
[ADR: Causal native commands](docs/ADR_CAUSAL_NATIVE_COMMANDS.md).
The 2026-07-23 DirectX device-reset diagnosis, prior-DLL reproduction, and
reversible live-test mitigation are recorded in the
[live stability incident](docs/LIVE_STABILITY_INCIDENT_20260723.md).
Independent preemption and the narrow safe-pause exception are recorded in
[ADR: Independent safety supervision](docs/ADR_SAFETY_SUPERVISOR.md).
Portable movement lifecycle and future-only patch authority are recorded in
[ADR: Stateful movement options](docs/ADR_STATEFUL_MOVEMENT_OPTIONS.md).
The cognitive-action boundary, source attribution, and advisor call policy are
recorded in
[ADR: Read-only guide-grounded strategic advisor](docs/ADR_STRATEGIC_ADVISOR.md).

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

- Native build/load and supervised evidence is specific to the pinned
  Kenshi/RE_Kenshi/KenshiLib versions and the current Windows host.
- `move_in_direction` is declared and bounded to 2,000 world units. Its
  targetless cross-language path is portable-tested, native-built, and
  installed. One live 36.5-degree, 30-unit probe proves exact dispatch identity,
  bounded pause handoff, native completion, plausible movement, a resulting
  frame, and safe final pause. Other bearings, distances, obstacles, and scenes
  are not thereby generalized. Selecting and executing a remote map destination
  is separately absent.
- Management screens can be entered, exited, and identified, but their domain
  contents and operations are not comprehensively modelled.
- The 224-entry native UI export and planner context are bounded. A busy screen
  can still require scrolling or closing a window.
- Item cells expose base value, not an authoritative final shop charge.
  Optional pre-purchase spending gates use that estimate; the actual debit is
  confirmed only after the at-most-once purchase from later money telemetry.
- A causally later observation prevents stale pre-action state from satisfying a
  postcondition, but generic success conditions are still planner-authored.
  Most actions do not yet have controller-owned effect predicates, so later
  correlated state can be mistaken for the intended effect.
- `use_game_binding(pause)` can toggle an unpaused game even when
  `allow_live_unpause_actions=false`, because that guard currently applies only
  to direct `PauseAction`. Game bindings use a hard-coded default key map rather
  than parsing the active `controls.cfg`.
- Safety preemption owns a verified pause cleanup, but ordinary stop, budget,
  failure, cancellation, and exception exits do not share a unified final-state
  owner; `LiveEnvironment.close()` does not manipulate Kenshi.
- Body-part wounds, bleeding rate, getting-eaten, imprisonment/enslavement,
  location name, current tasks, trader money, geometry occlusion, and distant
  world state remain unavailable or unvalidated.
- Broader native identity and safety behavior still needs repeated validation
  across recruit/dismiss/reorder/KO/death, save/load, and zone transitions.
- Alternate-resolution startup, repeated focus/input trials, multi-hour
  stability, and broad unsupervised strategy competence remain open.
- SendInput can fail when Windows integrity levels differ or foreground focus is
  denied.
- The mock world tests orchestration, not Kenshi strategy competence.

## License and project status

The repository is GPL-3.0-or-later because its native plugin is designed to link
against GPL-licensed KenshiLib. Kenshi is owned by Lo-Fi Games. This project is
unofficial and includes no game assets or binaries.
