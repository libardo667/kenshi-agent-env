# Coding-agent implementation brief

Copy this entire file into a capable coding agent while its working directory is
the repository root. The agent is expected to edit the repository, build what is
possible on the host, run tests, and leave an evidence-based implementation
report. It must not claim real-game success without running the manual checks.

---

You are the lead implementation engineer for **Kenshi Agent Environment**, a
research scaffold that lets a deliberative model interact with Kenshi through a
bounded environment API.

Your mission is to take the existing scaffold from its current tested mock state
to the strongest reliable implementation the host machine and installed Kenshi
tooling permit. Preserve the experimental boundary: Kenshi telemetry may be
read from game internals, but player actions must remain visible ordinary
keyboard and mouse operations. Do not turn this into an internal cheat API.

## Read first

Before changing code, read these files in order:

1. `README.md`
2. `STATUS.md`
3. `ARCHITECTURE.md`
4. `SECURITY_AND_SAFETY.md`
5. `docs/TELEMETRY_PROTOCOL.md`
6. `docs/LIVE_VALIDATION_CHECKLIST.md`
7. `native/KenshiAgentTelemetry/README.md`
8. `pyproject.toml`
9. all tests under `tests/`
10. the current native plugin source

Then run the baseline commands from the repository root:

```powershell
python -m pip install -e ".[dev]"
pytest
kenshi-agent doctor --config config/default.yaml
kenshi-agent run --config config/default.yaml --mode mock --planner heuristic --steps 40
```

Record the outputs before modifying anything. The scaffold was delivered with 20
platform-independent tests passing and a successful one-day mock run. If that is
not true on your host, diagnose the discrepancy before extending functionality.

## Non-negotiable invariants

These are architecture and safety requirements, not suggestions.

1. **The native plugin is read-only.** It may inspect validated game state and
   write telemetry. It may not issue tasks, select units, alter money, health,
   inventory, factions, AI, position, save state, game speed, or any other game
   state. Do not call internal action methods even when they are convenient.
2. **All player actions go through the normal interface.** Use Windows input
   injection only after policy validation. Directly invoking methods such as
   `setDestination`, `attackTarget`, `newPlayerTaskSelectedCharacters`,
   `setGameSpeed`, or `userPause` would invalidate the intended experiment.
3. **No Kenshi or MyGUI object may be dereferenced from a worker thread.** Sample
   on a verified game/UI thread. Copy into plain owned data. A worker may write
   those copied bytes, but it may not retain or follow game pointers.
4. **Dry-run remains the default.** Real input requires both the configuration
   gate and the explicit CLI flag. Do not weaken or remove either gate.
5. **F12 remains a hard emergency stop by default.** Check it before every
   primitive action and between steps of every macro.
6. **Missing data is unknown.** Never serialize an unavailable health value as
   zero, an unknown relation as neutral, or a failed inventory enumeration as an
   empty inventory.
7. **Capabilities gate trust.** Do not advertise a telemetry capability until
   its fields have passed live validation. An experimental value may be placed
   behind a disabled compile flag or emitted with a warning, but not presented
   as authoritative.
8. **Call the original hooked function.** Hook behavior must preserve the game
   path and must be guarded against reentrancy and title-screen/save-transition
   null states.
9. **Do not silently repair model output.** Schema-invalid decisions are planner
   failures. Log them and stop safely unless a separately evaluated repair
   policy is deliberately added.
10. **Do not claim evidence you did not collect.** Code inspection is not a live
    test. A compiled DLL is not a loaded DLL. A loaded DLL is not validated
    telemetry. A proposed action is not a successful action until the next
    observation confirms the expected state.

## Current architecture

The system intentionally separates five responsibilities:

```text
native Kenshi sampling -> versioned telemetry
window capture          -> visual observation
planner                  -> one structured decision
policy + skill compiler  -> bounded primitive actions
Windows input            -> visible Kenshi interaction
```

Every boundary is written to JSONL. SQLite memory stores facts, episodes,
hypotheses, and revisable commitments. The environment interface is:

```python
reset() -> Observation
observe() -> Observation
step(Action) -> Transition
close() -> None
```

Do not collapse these layers. A model should never receive a raw controller
object. An input controller should never decide strategy. Native code should
never know about prompts or models.

## Known upstream constraints to verify

The scaffold was prepared against the maintained `BFrizzleFoShizzle/KenshiLib`,
`BFrizzleFoShizzle/RE_Kenshi`, and `BFrizzleFoShizzle/KenshiLib_Examples`
projects as they existed in July 2026. At that point, plugin examples still used
an x64 Visual C++ 2010 (`v100`) platform toolset and Release builds, even though a
newer Visual Studio IDE could host the project.

Treat that as a starting fact, not an eternal one. Before native work:

- inspect the current upstream default branches and releases;
- record exact repository URLs, release/tag or commit SHA, retrieval date,
  Kenshi executable version, and installed mod list;
- compare the current HelloWorld project and plugin packaging layout against
  this repository;
- review upstream licensing and keep the root GPL-3.0-or-later unless a lawyer or
  a carefully separated distribution architecture supports another decision;
- create or update `docs/UPSTREAM_LOCK.md` with the exact versions actually used.

Do not follow old forum installation instructions when current example projects
or release notes disagree.

## Phase 0 — Establish a reproducible baseline

Goal: prove that repository-local behavior is deterministic before touching
Kenshi.

Tasks:

- Run all tests.
- Run Ruff and mypy; repair real issues without weakening strictness globally.
- Export schemas and confirm the working tree changes only when schemas actually
  change.
- Run the heuristic mock episode at least five times with fixed seeds.
- Add a small command or test that verifies a session log can be replayed and
  summarized.
- Ensure tests do not depend on a globally installed package or a pre-existing
  `runs/` directory.

Acceptance:

- All platform-independent tests pass.
- Fixed seeds produce stable high-level outcomes.
- Every run has a parseable `events.jsonl`, at least one screenshot, a final
  summary, and no unhandled exception.
- A failing planner and a policy-rejected action both produce explicit log
  events and safe termination.

## Phase 1 — Lock dependencies and build context

Goal: make native failures attributable to one known toolchain.

Tasks:

- Create `docs/UPSTREAM_LOCK.md` containing exact upstream revisions,
  checksums/paths of downloaded dependency bundles, toolset version, Windows SDK,
  Kenshi executable version, RE_Kenshi version, active mods, and installation
  path.
- Add a PowerShell doctor script that checks `KENSHILIB_DIR`, expected include
  files, `kenshilib.lib`, MSBuild, and availability of the v100 x64 toolset.
- Preserve compatibility with the official/current example project layout.
- Do not vendor copyrighted game binaries or user-specific absolute paths.

Acceptance:

- Another engineer can reconstruct the native build context from the lock file.
- Missing v100 or dependency files fail with a direct diagnostic rather than a
  linker avalanche.

## Phase 2 — Compile and load the minimal native plugin

Goal: establish that the DLL loads before expanding telemetry.

The current source exports `startPlugin`, writes `plugin_status.json`, and hooks
`PlayerInterface::update` using the maintained KenshiLib hook API. Confirm this
against current upstream headers and examples. Do not assume a function address
or calling convention from memory.

Tasks:

- Build **Release | x64** first.
- Match compiler/runtime settings used by the current HelloWorld example.
- Stage the DLL using the exact current RE_Kenshi plugin layout.
- Launch Kenshi with a disposable profile/save.
- Inspect the appropriate RE_Kenshi/Kenshi logs for loader or hook errors.
- Confirm `plugin_status.json` transitions from `starting` to `ready`.
- Exercise title screen, new/load game, save, return to title, and exit.
- Add null, reentrancy, and save-transition guards where evidence requires them.

Acceptance:

- The plugin loads and unloads without a crash.
- Removing it restores baseline behavior.
- The status file reports errors clearly when hook installation fails.
- No telemetry field beyond plugin status is required yet.

If the existing `PlayerInterface::update` hook is incompatible, choose another
verified main/UI-thread hook from current upstream examples or symbols. Document
why, and preserve the thread invariant. Do not use a worker thread to poll game
objects as a shortcut.

## Phase 3 — Validate the current partial telemetry

Goal: confirm each already-emitted field before adding more.

The current native source attempts to export:

- `game.loaded`
- `game.paused`
- `game.speed_multiplier`
- `game.money`
- camera position and center
- squad index, name, selection, alive/conscious/down/crippled/getting-eaten state
- position, movement speed, and food-item count

For each field, build a validation table with:

- accessor used;
- thread/hook context;
- null/loading preconditions;
- expected units and ranges;
- manual action used to change it;
- observed before/after values;
- pass/fail and any discrepancy.

Do not validate several ambiguous fields as one bundle. A correct squad count
does not prove identity stability; a plausible position does not prove axes or
zone-transition behavior.

Required trials:

- pause and unpause at least 20 times;
- switch all three speed controls at least 20 times;
- select different portraits repeatedly;
- move, stop, carry, become injured, become unconscious, recover, recruit,
  reorder, dismiss, save/load, and change zones where feasible;
- compare money and food counts across buy/sell/eat actions;
- leave telemetry running for at least ten minutes and inspect frame time/logs.

Only retain a capability when the field group passes. Otherwise remove the
capability and include a warning until repaired.

## Phase 4 — Expand telemetry incrementally

Goal: expose enough player-observable state for meaningful play without becoming
omniscient.

Implement in this order. Complete live validation for one group before starting
the next.

### 4A. Game and time context

Desired fields:

- in-game day/hour/minute or an equivalent stable game-time representation;
- current known town/zone for selected characters;
- loading/title-screen state;
- current pause and speed confirmation;
- money source semantics documented.

Do not expose hidden global map state. Location should reflect what the player or
selected character can reasonably know.

### 4B. Stable squad identity and basic task state

Replace provisional `squad:<index>` identity with a validated stable handle or
serialized identifier. It must survive portrait reorder and ordinary save/load,
or be explicitly documented as episode-local if that cannot be achieved.

Desired fields:

- stable id, display name, squad/platoon;
- selected/tracked state;
- position, movement speed, conscious/down/dead state;
- current visible goal/order text when safely available;
- carrying/carried state if player-visible.

Never persist raw pointer addresses as identity.

### 4C. Medical, hunger, and inventory summary

Desired fields:

- hunger with documented direction and range;
- body-part current/max HP and wound components;
- bleeding and deterioration indicators;
- missing limbs and getting-eaten state;
- food and medical supply summary;
- encumbrance and inventory capacity when visible;
- detailed inventory only when enumeration is stable and useful.

Start with summary counts. A full grid dump is not automatically better and can
consume prompt budget while exposing unstable internals.

### 4D. UI and modal state

Desired fields:

- active major panel;
- inventory/map/stats/build mode;
- dialogue open and visible choices;
- context menu visible and visible entries;
- confirmation or error modal;
- current client dimensions and UI scale if detectable.

MyGUI access must remain on the UI thread. Do not retain widget pointers across
frames without proving their lifetime rules. Prefer extracting plain labels and
booleans during the verified update hook.

### 4E. Nearby visible entities

Desired fields:

- entities reasonably visible to the controlled squad or current camera;
- name/type/faction label where player-visible;
- approximate distance;
- hostility only when known through player-facing relation or observed combat,
  not through hidden omniscient AI intent;
- conscious/down state when visually/legibly knowable.

Use range caps and result caps. Do not dump every loaded character in the zone.
Define and document the epistemic filter. If KenshiLib only provides loaded
objects rather than visibility, label the capability accordingly and keep it
off by default until a conservative filter exists.

### Implementation discipline for every new native field

1. Locate the current header/symbol and record it.
2. Establish ownership, lifetime, and thread assumptions.
3. Add one optional schema field.
4. Emit it behind a narrowly named capability.
5. Add a parser fixture and Python test.
6. Build and load the plugin.
7. Perform a controlled before/after trial.
8. Record evidence in `docs/NATIVE_FIELD_VALIDATION.md`.
9. Only then allow planners to depend on it.

When uncertain, leave a TODO with the exact unknown. Do not invent field offsets,
reinterpret unexplained numeric values, or copy an old offset table without
matching the executable.

## Phase 5 — Harden transport and freshness

Goal: make stale or torn telemetry detectable and recoverable.

Tasks:

- Preserve atomic replacement.
- Add a session/boot id so a sequence reset after plugin reload is unambiguous.
- Track both timestamp age and sequence progress in Python.
- Distinguish no file, invalid JSON, protocol mismatch, stale timestamp, frozen
  sequence, and source restart.
- Consider a small heartbeat/status file separate from full telemetry.
- Measure write cost. If main-thread file I/O causes hitches, copy a complete
  plain snapshot on the game thread and hand only owned bytes to a writer thread.
  Never hand game pointers to it.
- Add bounded backoff; do not spin when the file is unavailable.

Acceptance:

- Killing or disabling the plugin produces a stale/frozen diagnostic and causes
  pause or stop rather than blind clicks.
- Readers never parse partial JSON during stress tests.
- Plugin restart is detected without falsely treating an old high sequence as
  current.

## Phase 6 — Validate screenshot and Windows input

Goal: prove the normal-interface control path independently of the model.

The implementation uses client-area screenshots and Windows SendInput. Windows
may reject or suppress injection when foreground focus or integrity levels do
not match.

Tasks:

- Add doctor checks for exact window title match, client dimensions, foreground
  state, and integrity-level mismatch when feasible.
- Confirm screenshots contain only the Kenshi client area.
- Handle borderless, windowed, and multi-monitor virtual-desktop coordinates.
- Add tests for coordinate conversion as pure functions.
- Fail closed when client dimensions are zero, window focus cannot be obtained,
  or the title filter is ambiguous.
- Confirm key-down/key-up cleanup after exceptions. A failed macro must not leave
  Ctrl, Shift, Alt, or a mouse button logically held.
- Test F12 before action, during multi-step macro, and while the model is idle.
- Keep all execution disabled unless both gates are active.

Acceptance:

- In a disposable save, a manually chosen harmless key succeeds repeatedly.
- Dry-run produces identical decisions and receipts except `executed=false` and
  sends no input.
- Loss of focus and emergency stop abort safely.
- Coordinate tests cover negative virtual-desktop origins and multiple monitors.

## Phase 7 — Build skills as verified procedures

Goal: give the planner reliable, higher-level actions without hiding failures.

Do not begin with broad skills such as `buy_food` or `travel_to_squin`. Build
small procedures with observable preconditions and postconditions:

1. `toggle_map`
2. `toggle_inventory`
3. `focus_selected`
4. `select_portrait(index)`
5. `pause_if_unpaused`
6. `set_speed(level)`
7. `choose_dialogue_option(index)`
8. `click_context_menu_entry(label)` only after label extraction is reliable

Each skill must define:

- preconditions;
- primitive expansion;
- resolution/UI-scale assumptions;
- expected postcondition;
- timeout;
- retry policy;
- recovery/abort behavior;
- maximum primitive count;
- evidence that it worked.

A macro is not successful because its clicks were sent. The next observation
must confirm its postcondition. Add a skill-result state if needed rather than
burying verification in planner prose.

For coordinate skills, use semantic anchors calibrated to a specific client
resolution and UI scale. Refuse execution on mismatch. Prefer visible target
detection over fixed coordinates when reliable, but do not add brittle OCR as a
silent source of truth.

Only after UI primitives are robust should you compose gameplay skills such as
first aid, eating, shopping, looting, or travel.

## Phase 8 — Planner and memory integration

Goal: make model behavior inspectable and comparable, not merely entertaining.

Tasks:

- Preserve the heuristic baseline.
- Keep model output constrained to `PlannerDecision`.
- Send a bounded observation, explicit available skills, recent action outcomes,
  and only relevant memories.
- Do not ask for or store private chain-of-thought. A concise rationale should
  name the evidence and intended effect.
- Add explicit recent-attempt tracking so the planner can see two failed
  repetitions.
- Preserve memory types: fact, episode, hypothesis, commitment.
- Add contradiction handling. New evidence should supersede or qualify an old
  fact rather than coexist as two unquestioned truths.
- Keep commitments revisable and separate from facts.
- Log model, prompt version/hash, latency, token usage when available, response
  id, schema validity, and any API error.
- Add a deterministic fake planner for tests. Never require a live API key in CI.

Acceptance:

- The same recorded observation can be replayed into multiple planners.
- Planner failures terminate safely and remain distinguishable from policy and
  environment failures.
- No hidden repair step changes the semantic action without being logged.

## Phase 9 — Benchmarks and failure attribution

Goal: produce evidence about agent capability.

Implement the bundled one-day survival benchmark and at least the first five
bounded tasks in `docs/EXPERIMENTS.md` using controlled starts or documented save
snapshots.

For every run record:

- exact model and prompt hash;
- exact game/mod/plugin versions;
- save/scenario identifier;
- observation condition;
- available skills;
- action budget;
- success/failure and survival time;
- human interventions;
- failure label: perception, planning, control, native integration, or external
  API/system.

Do not use one dramatic run as the main metric. Run repeated trials and report
all failures. Preserve notable episodes separately as qualitative case studies.

Add tooling that can export a compact CSV/JSON result table from session logs.
Screenshots and full prompts remain private artifacts unless intentionally
redacted.

## Phase 10 — Packaging and operator documentation

Goal: make the system reproducible without pretending it is turnkey.

Tasks:

- Provide a clean bootstrap script for Python.
- Provide an upstream-lock and native-toolchain doctor.
- Stage, but do not blindly install, native binaries.
- Document exact manual installation/removal for the verified RE_Kenshi release.
- Document save backup and disposable-test procedures.
- Add a changelog entry with protocol changes.
- Keep secrets and user paths out of committed files.
- Preserve GPL notices and third-party attribution.

## Native technical cues already established

Use current upstream headers as the source of truth, but the scaffold was based
on these maintained interfaces:

- plugin entry point: `__declspec(dllexport) void startPlugin()`;
- logging: `DebugLog` and `ErrorLog`;
- hook API: `KenshiLib::GetRealAddress` and `KenshiLib::AddHook`;
- global world pointer: `GameWorld* ou`;
- `GameWorld`: pause/speed and camera accessors, plus `PlayerInterface* player`;
- `PlayerInterface`: `update`, selected-character handle, and player-character
  enumeration;
- `Character`/`RootObjectBase`: name, validity, position, movement speed,
  consciousness/down state, money, food count, and related accessors.

Important: `GetRealAddress` is documented not to work directly with virtual
functions. The current hook targets the non-virtual `PlayerInterface::update`.
When calling virtual accessors on a live object, validate that the object's
vtable and lifetime are sound for the current state. Do not reinterpret a
virtual symbol as a hook target without a verified non-virtual wrapper or an
upstream-supported technique.

## Python quality requirements

- Support Python 3.11 and later.
- Keep imports safe on non-Windows systems; Windows-only classes may fail at
  instantiation, not package import.
- Maintain strict Pydantic models with `extra="forbid"`.
- Add unit tests for every policy rule and pure coordinate conversion.
- Use temporary directories in tests.
- Do not make CI depend on Kenshi, Windows, a GPU, or an API key.
- Keep live/manual tests clearly separated and opt-in.
- Preserve UTC-aware timestamps.
- Keep JSONL append-only and crash-tolerant.
- Add migrations before changing SQLite schema in a way that can break existing
  memory databases.
- Avoid dependencies unless they replace meaningful custom complexity.

## Manual test matrix

At minimum, test these game states before calling the native layer usable:

- title screen;
- loading screen;
- paused/unpaused at all speeds;
- one-character and multi-character squad;
- selected/unselected/reordered character;
- moving, stationary, carrying, sleeping, imprisoned, enslaved;
- combat, unconsciousness, recovery, death, missing limb, being eaten where a
  disposable scenario permits;
- inventory and dialogue panels open/closed;
- save, load, import if relevant, return to title, and exit;
- indoor/outdoor, town/wilderness, and zone transition;
- common modded UI or animation changes if those mods are in the declared test
  set.

Do not create dangerous states in a valued save. Use backups or purpose-built
FCS scenarios.

## Change discipline

For each coherent change:

1. State the hypothesis or defect.
2. Add or identify a failing automated/manual check.
3. Make the smallest change that resolves it.
4. Run the relevant focused tests.
5. Run the whole platform-independent suite.
6. Update schema/docs when semantics change.
7. Record evidence and remaining uncertainty.

Do not perform broad refactors while native behavior is still unverified. Keep a
known-good minimal plugin branch or artifact so telemetry expansion can be
bisected.

## When blocked

Do not guess. Produce a concrete blocker note containing:

- exact file/symbol/tool command;
- exact error or observed contradiction;
- versions involved;
- hypotheses ranked by evidence;
- smallest next experiment;
- safe fallback that preserves the mock/runtime path.

A partial field set with honest capabilities is preferable to a rich but
unreliable world dump.

## Required final report

At the end of your work, provide:

1. **What changed** — grouped by Python runtime, native plugin, control, planner,
   tests, and docs.
2. **Evidence** — exact commands run and concise results.
3. **Live validation status** — each checklist section marked passed, failed, or
   not run. Never merge “not run” into “passed by inspection.”
4. **Protocol changes** — fields/capabilities added, removed, or redefined.
5. **Safety review** — confirmation that native actions remain read-only and
   live-input gates/emergency stop remain intact.
6. **Known failures and uncertainties** — with reproduction steps.
7. **Next highest-value experiment** — one concrete task, not a vague roadmap.

## Definition of done for the first real milestone

The first milestone is complete only when all of the following are true:

- platform-independent automated tests pass;
- exact upstream/toolchain versions are locked;
- the native DLL builds Release x64 and loads in the target Kenshi installation;
- plugin status and telemetry remain stable through title/load/save/title/exit;
- current partial fields pass the documented manual trials or have their
  capabilities removed;
- Python detects stale/frozen telemetry and fails closed;
- live mode can capture the Kenshi client and propose decisions in dry-run;
- one harmless normal-interface key action succeeds repeatedly under both safety
  gates;
- F12 aborts before the next primitive action;
- a complete run can be replayed and summarized;
- the final report distinguishes what was automated, manually observed, and not
  tested.

Do not redefine the milestone downward to hide missing live evidence. Preserve a
working mock environment even if the native toolchain is blocked.

---

Begin by reading the repository, running the baseline, and writing a short
implementation ledger in `runs/engineering-ledger.md` containing the current
host, toolchain, upstream versions, test results, and the first verified risk.
Then proceed phase by phase, keeping the ledger current.
