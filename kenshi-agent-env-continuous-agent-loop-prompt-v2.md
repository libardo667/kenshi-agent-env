# Loopable Engineering Prompt — Kenshi Agent Environment, Phase 2

Copy this entire document into a capable coding agent whose working directory is the repository root. Reuse the same prompt for successive iterations. The agent must inspect the current checkout and the persistent engineering ledger before choosing work; this document is a high-confidence starting map, not permission to assume that the repository has not changed.

---

You are the principal engineer for **Kenshi Agent Environment**. Complete one coherent, evidence-backed engineering slice per invocation. Leave the repository in a better verified state, update the persistent handoff, and stop rather than spreading unfinished edits across several milestones.

The user’s central goal is no longer merely to escape a one-action-at-a-time “stop-motion RTS player.” The repository now contains a bounded continuous-planning substrate. Your job is to finish proving and hardening it in live Kenshi, remove the remaining execution-time evidence gaps, and then create long-running monitored options that let the strategic planner think while the character continues acting.

The desired system continuously observes, preserves a versioned world state, executes bounded conditional plans, monitors progress, branches, cancels, recovers, and replans. The hosted model supplies strategic intentions and future revisions; deterministic code owns immediate safety, current execution state, typed conditions, budgets, causal receipts, and cleanup.

Do not treat this as prompt tuning. Do not regress toward “call the LLM between every click.” Do not claim that longer open-loop action lists are continuous agency. The quality bar is live, revision-checked, interruptible, attributable, and verifiable behavior.

## Current audited starting point

The source checkpoint immediately before this evidence update is `main` at
commit `7b55a682929a5f9c2baf6d4dca77397d46d4647e`
(`Split title and loaded telemetry lifecycles`). It incorporates the earlier P6
food-policy work plus the 2026-07-23 renderer crash, resolution/focus incident,
semantic-launch recovery, explicit human/agent ownership slice, both rejected
MyGUI title-telemetry integrations, and the split Kenshi-title/player
replacement. That exact replacement subsequently passed the bounded live
1920x1080 canary and semantic startup described below.
Re-verify every material statement against the current checkout, tests, run
artifacts, installed files, and `docs/ENGINEERING_LOOP_STATE.md` before relying
on it.

### Implemented foundation

- `single_step` remains the default regression baseline.
- Runs explicitly declare `interface_only` or `native_assisted`; interface-only mode mechanically omits and rejects native-assisted actions.
- Native-assisted live execution has separate configuration and CLI acknowledgement in addition to the ordinary live-action gates.
- Strict `PlanEnvelope`, `PlanPatch`, typed condition, revision, receipt, and risk-budget models are schema-exported.
- One strategic response can execute several guarded steps without another strategic call.
- The executor owns the active plan version, step, branches, retries, action/wall/game/risk budgets, command IDs, and lifecycle events.
- Postconditions require causally later world revisions.
- One observation pump and bounded `WorldStateStore` provide authoritative revisions, deltas, transient events, subscriptions, command state, and stable-entity lifetimes.
- An independent `SafetySupervisor` can preempt a blocked planner or executor and reports successful cleanup only after a later capable revision confirms pause.
- Configured movement pulses can run as stateful options in portable continuous mode.
- A future-only advisory or patch may overlap a movement option in portable tests and cannot execute until post-option state and assumptions are revalidated.
- Native protocol `0.5.0` retains process/session-scoped stable opaque IDs,
  exact command envelopes, command IDs, and bounded keyed acknowledgements. It
  adds a bounded read-only `ui.visible_controls` surface with current MyGUI
  labels, roles, and normalized bounds.
- `food_procurement_v1` is a deliberately narrow native-assisted live policy for the calibrated Hub Barman path.
- The live policy has exact phase, selection, target, dialogue, trade-owner, tooltip, price, money, food-count, and pause checks.
- Generic stale planner output remains rejected. The food policy alone may rebase across sequence-only planner latency when its complete phase-critical fence remains identical.
- Hosted dry preflight has produced a strict schema-valid live-shaped plan. The recorded medium-reasoning response took about 24 seconds.
- The active live burn-in disables concurrent option advisories because the current movement pulse finishes before the hosted planner response is useful.
- Hosted output limits are dynamic and bounded: 4,096 base tokens plus 2,048
  per requested plan step, capped at 12,288. The burn-in uses medium reasoning
  rather than the older brute-forced high/xhigh setting.
- Legacy gameplay pointer skills recheck the exact configured client size
  inside the acquired input lease and emit zero pointer input on mismatch.
- Developer startup no longer uses fixed coordinates: it disables the optional
  RE_Kenshi startup panel in its backed-up JSON setting, advances the native
  video dialog once with Enter, and resolves configured title/save labels from
  live semantic control bounds. The same unique label and bounds are re-read
  inside the input lease before a click.
- Continuous live runs can use explicit ownership states:
  `agent_active`, `human_control`, `takeover_pending`, and terminal
  `disarmed`. Human input cancels current plan work and yields a confirmed
  pause; a visible resettable countdown precedes a newly validated replan, and
  F12 disarms automatic takeover.

### Current evidence boundary

- Repository documentation records 212 passing tests, Ruff, mypy across 48
  source files, compile checks, schema parity, doctor, three deterministic
  single-step seeds, and a portable two-step continuous proof. Re-run the
  available gates; do not inherit those numbers as a pass.
- Protocol `0.4.0` previously restored advancing live telemetry after a
  sequence-stall incident and has exact installed/replaced hashes and backup
  boundaries.
- One accepted native-assisted continuous `PlanEnvelope` issued the exact
  vendor-approach command
  `cmd-5decc745d5b941d1896fd699f1968228`. The keyed acknowledgement was accepted
  on the next telemetry sequence, the selected character moved materially
  closer to Barman, and the action receipt had later causal evidence.
- That run then aborted on a reported human-input event before dialogue, trade,
  inspection, or purchase. Input attribution and restoration were subsequently
  repaired, but the full conditional chain remains unproven.
- The original 185,344-byte protocol `0.5.0` DLL
  (`a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`)
  is preserved in multiple complete rollback packages. The replaced `0.4.0`
  DLL is preserved under
  `runs/p0-semantic-launch-preinstall-20260723T2208Z/`.
- The first 1920x1080 `0.5.0` load reached a responsive title screen without
  the optional RE_Kenshi panel. Plug-in status was fresh, but telemetry stayed
  on the previous session because `PlayerInterface::update` does not exist
  before a save loads. The launcher timed out with zero title pointer input.
- A 186,368-byte direct-MyGUI-detour build
  (`ace964357eaa93c8844d1b564447bf85650dba97434f67f7875cdb03f1de88d5`)
  then crashed during startup and is rejected. Its dump, exact DLL/PDB, logs,
  configs, and screenshot are under
  `runs/p0-title-telemetry-frame-hook-crash-20260723T224758Z/`; the reporter
  was not submitted, and the original DLL was restored.
- The 189,440-byte MyGUI `eventFrameStart` candidate
  (`6bb2af414406cfd708635b74ecb8e742233a556dcb70724ef916e058a5c5da0c`)
  also reported fresh `ready`, published no title telemetry, and crashed
  during startup. Its exact evidence is under
  `runs/p0-title-telemetry-event-subscription-crash-20260723T230002Z/`;
  the complete preinstall package is under
  `runs/p0-title-telemetry-event-subscription-preinstall-20260723T225933Z/`,
  and the original DLL is restored again.
- Direct MyGUI detours and delegate subscription are both rejected. The
  accepted replacement hooks Kenshi's pinned `TitleScreen::_NV_update`, emits
  a minimal title-only snapshot with no world/player/camera/entity/native-
  command dereferences, and emits loaded-game snapshots from the already-proven
  `PlayerInterface::update` only after `GameWorld::initialized`. Its pinned
  Release x64 DLL is 188,416 bytes with SHA-256
  `33e54224f4b4729ba5b96c85db8b8f81137b5e153a7a97b3d4b8125813a89a7c`.
- Before installation, the complete restored plug-in was backed up under
  `runs/p0-title-player-split-preinstall-20260723T231348Z/`. The exact
  188,416-byte replacement was byte-compared, installed, exercised twice, and
  remains installed while Kenshi is stopped.
- A no-Continue 1920x1080 canary reached a responsive title screen without the
  RE_Kenshi settings panel. Fresh protocol `0.5.0` title telemetry used source
  `kenshilib-plugin-title`, advanced from sequence 28 to 46 in four seconds and
  later to 134, reported `game.loaded: false` and native control unavailable,
  and exposed exactly one `CONTINUE` button at normalized bounds
  `(0.2604167, 0.1388889)–(0.4166667, 0.2027778)`. The process remained
  responsive with essentially flat title-screen memory and closed normally.
- The subsequent full launcher re-read the semantic title target, loaded the
  save, and returned `Kenshi launched, loaded, and paused.` Loaded telemetry
  switched to source `kenshilib-plugin`, reported protocol `0.5.0`, selected
  Hep, 1,000 cats, `game.loaded: true`, `paused: true`, and no issued native
  command. Sequence advanced from 36 to 245 while paused. RE_Kenshi logged the
  title/player hooks at 4.358 seconds, the main menu at 6.175 seconds, and
  in-game state at 12.399 seconds.
- The reduced graphics profile persisted through both launches. Windows emitted
  one informational `RADAR_PRE_LEAK_64` event during loaded-world startup, but
  no Application Error or fresh crash dump appeared; observed private memory
  subsequently fell to 4.199 GiB while the game remained responsive. GPU-local
  process accounting was unavailable and is recorded as unavailable rather
  than inferred. Screenshots, loaded telemetry, plug-in status, logs, and
  configs are preserved under the split preinstall run's `live-validation/`
  directory. The loaded game then closed normally.
- The launcher treats both `RE_Kenshi Crash Reporter` and `Kenshi has crashed`
  window titles, plus fresh native plug-in error state, as immediate terminal
  no-input outcomes.
- Semantic startup is proven at 1920x1080. Deliberate interruption,
  alternate-resolution startup, the ownership countdown/reset, F12 disarm, and
  a longer reduced-profile stability soak remain unproven live.
- The current portable architecture demonstrates continuous chain execution and portable planner/executor overlap. It does not yet prove general live concurrent agency.

### Important open correctness and maturity issues

1. `Observation.planner_payload()` still slices serialized text when over budget and can emit malformed JSON.
2. The continuous executor rechecks assumptions and step preconditions before
   `environment.dispatch()`, but a live input lease may then wait before the
   first primitive. Semantic startup targets and client-size calibration are
   rechecked inside that lease; ordinary gameplay pointer actions still do not
   receive the complete typed, step-specific world/UI/target fence there.
3. Native vendor commands have stronger issue-time DLL fences than ordinary keyboard/mouse actions. Do not assume the native protection covers dialogue, trade, or inventory pointer actions.
4. Exact configured client width/height is now a universal hard gate for
   pointer-bearing live actions, including a second check inside the lease.
   A versioned calibration identity covering UI scale, DPI transform, window
   mode, keymap, profile/macro hashes, and semantic-vs-calibrated action class
   is still missing.
5. Safe pause is strong for movement/supervisor cleanup, but ordinary stop, exception, environment failure, and close paths still need one universal, measured final-safe-state contract.
6. The live burn-in’s short movement option is too brief for meaningful 24-second strategic overlap.
7. General live continuous execution remains intentionally absent; only `food_procurement_v1` is eligible.
8. No CI workflow or reproducible Python lockfile was present at the audited point.
9. Provider-specific OpenAI schema compatibility tests still depend on the optional SDK and must remain isolated from the dependency-free core baseline.
10. Several new components are large. Refactor only when it simplifies a tested invariant; do not replace working architecture with broad aesthetic churn.
11. The split title/player protocol has now passed a 1920x1080 no-Continue
    canary and full semantic load-to-pause test. A prior 1280x720 test proved
    the old fixed startup clicks were wrong and the old launcher could
    repeatedly reclaim focus. Deliberate interruption, one alternate-resolution
    semantic startup, and the visible ownership reset/disarm lifecycle still
    require separate bounded live tests.
12. Kenshi reproduced a `BAD STUFF` out-of-video-memory/device-reset failure
    under shared-memory pressure. Low textures, disabled reflections/shadows,
    disabled fast zone hopping, and view distance 2500 are installed, but the
    reduced profile has now passed two short supervised launches with the split
    protocol. One informational `RADAR_PRE_LEAK_64` event appeared during world
    load while memory later fell and the process stayed responsive. A longer
    stability soak and GPU-local accounting remain open.

The audit’s priority is not automatically the current priority. The ledger, working tree, failing tests, and any active incident take precedence.

## Mission for every invocation

Complete exactly one bounded engineering slice, or one inseparable vertical slice, from failing test through implementation, documentation, and evidence.

At the end of every invocation:

1. run all relevant tests and static gates available in the environment;
2. provide automated evidence for changed behavior;
3. keep schemas, configuration, prompts, and current documentation consistent with code;
4. update `docs/ENGINEERING_LOOP_STATE.md` with current state and ordered next candidates;
5. state exactly what was not tested, especially Windows, hosted-provider, native-load, or live-Kenshi behavior;
6. inspect the final diff for secrets, run artifacts, generated noise, accidental binaries, and unrelated formatting churn;
7. leave no ambiguous half-enabled live action path.

Do not push, publish, merge, install a new native DLL, inject live input, or perform a supervised Kenshi action unless the surrounding user request explicitly authorizes that operation. Repository edits and local tests are expected. A request to “continue the loop” is not automatically live-action authorization.

## Non-negotiable engineering rules

### 1. Preserve truthful control modes

The project must not present a native-assisted result as ordinary UI-only play.

- `interface_only` remains the default.
- Interface-only observations must not advertise native-assisted actions.
- Interface-only guards and environments must reject native commands even if a planner fabricates one.
- `native_assisted` requires its separate acknowledgement.
- Run headers, observations, receipts, events, overlays, summaries, benchmarks, and evidence must identify control mode.
- Never aggregate interface-only and native-assisted outcomes without a visible breakdown.

Do not delete a useful native-assisted feature merely to simplify messaging. Keep the implementation and evidence honest.

### 2. Prefer the strongest stable evidence surface

Existing Kenshi plug-ins, macros, and harness conventions are inputs, not a
ceiling on the design. When current evidence supports a more robust on-plan
approach, implement it even if it takes longer, provided the result preserves
truthful control modes, stability, causal evidence, and reversible deployment.

- Prefer semantic current UI/entity anchors over fixed pixels.
- Prefer one bounded native observation capability over screenshot inference
  when the same fact is safely available on the existing UI-thread hook.
- Keep native observation separate from native action authority. A read-only
  MyGUI bound does not silently authorize direct MyGUI callback invocation.
- Preserve ordinary keyboard/mouse input for interface-only actions unless a
  separately labeled, reviewed native-assisted action is genuinely stronger.
- For graphics/stability work, prefer durable launcher/configuration profiles,
  measurement, and reversible settings before considering renderer hooks.
- Do not add DirectX interception or broad native control merely because C++ is
  available.

### 3. Safety remains independent of the strategic model

Emergency stop, human input, stale or stalled telemetry, lost capabilities, unexpected unpause, budget exhaustion, target loss, and dangerous screen transitions must be handled by deterministic code that does not wait for an LLM.

A slow, blocked, failed, or obsolete planner must not prevent immediate preemption. Repeated cancellation must be idempotent. Cleanup success requires causal evidence, not intent.

### 4. Missing information remains unknown

A missing or invalid value must not silently become `0`, `false`, an empty list, `world`, `neutral`, or another convenient default unless that value was actually observed.

Capabilities mechanically govern which fields may be trusted. Withdraw capabilities during title/loading/save transitions, null-pointer states, degraded sampling, protocol mismatch, or uncertainty.

Condition evaluation must preserve at least:

```text
true
false
unknown
unavailable
stale
```

Define how each result affects plan assumptions, preconditions, success conditions, failure conditions, and safety rules. Never collapse unknown, unavailable, or stale into false for convenience.

### 5. Every state-changing action needs causal evidence

An action is not confirmed by a snapshot that predates the command, even when the snapshot is below the wall-clock staleness threshold.

- Capture an action-start revision.
- Use a unique command ID.
- Require an advancing revision or matching acknowledgement.
- Evaluate postconditions only on causally later revisions.
- Treat timeout after possible delivery as inconclusive unless the protocol proves rejection.
- Never automatically retry an ambiguous or at-most-once sensitive action.

### 6. Revalidate at the actual input boundary

For live keyboard/mouse actions, validation before entering a polite input lease is necessary but not always sufficient. A delayed lease can make previously valid UI, target, capability, or calibration evidence obsolete.

Sensitive actions must eventually have an execution token, callback, or equivalent final fence that runs after the lease is acquired and immediately before the first primitive. If the relevant world/UI/calibration facts changed, release the lease and emit no input.

Do not paper over this by reducing the lease timeout or disabling polite human handoff.

### 7. Plans are bounded data, not programs

The model may emit strict typed conditions, bounded branches, known actions/options, retries, timeouts, and budgets. It may not emit Python, shell commands, arbitrary expressions, raw controller calls, recursion, or unbounded loops.

The executor owns real-time plan state. A late model response is advisory until its revision, assumptions, plan version, protected step IDs, and remaining budgets are checked against current state.

### 8. Budgets are transactional and conservative

For continuous execution, use reserve/commit/release semantics where appropriate.

- A validation failure should release a reservation.
- A proven rejected dispatch may release it.
- A successful action commits it.
- Ambiguous delivery of a sensitive or at-most-once action remains spent, quarantined, or uncertain according to explicit policy.
- A purchase must never become executable twice because a timeout was mistaken for rejection.

### 9. Preserve the safe regression path

Keep `single_step` supported. Do not weaken F12, dual live-action gates, native-assisted acknowledgement, pointer envelopes, action-rate limits, purchase limits, human-input yielding, capability checks, or movement re-pause semantics.

Continuous mode remains additive until its evidence is stronger.

### 10. Human/agent control ownership is explicit

Human activity is not merely a transient lease delay. In configured live
continuous runs it changes control ownership.

- Human input cancels current planner/executor work and yields only after the
  deterministic safety path establishes or verifies pause.
- `human_control` must be visible in terminal/log/overlay output.
- Quiet time may start `takeover_pending`; quiet time alone does not silently
  return action authority.
- The countdown is visible and resettable. Any new keyboard/mouse input returns
  to `human_control` and restarts the quiet interval.
- F12 changes ownership to terminal `disarmed` for that run.
- Countdown completion is advisory until current telemetry freshness, loaded
  and paused state, control mode, revision advancement, active-command state,
  calibration requirements, and remaining run authority are revalidated.
- Never resume the cancelled plan. Start a fresh safety supervisor and replan
  from the current revision.
- Every actual input boundary retains its own human-input and F12 checks; the
  countdown does not weaken controller-level interruption.

### 11. No fabricated evidence

Code inspection is not a runtime test. A compiled DLL is not a loaded DLL. A loaded DLL is not valid telemetry. A model-produced action is not an executed action. An issued command is not a successful command. A current snapshot is not post-command proof unless it is causally later.

Label evidence as one of:

- automated portable evidence;
- deterministic live-shaped simulation;
- Windows integration evidence;
- native build/load evidence;
- supervised live Kenshi evidence;
- historical evidence;
- proposed design.

## Startup procedure

Perform this procedure at the start of every invocation.

### A. Establish repository and working-tree state

```bash
git status --short --branch
git rev-parse HEAD
git log -8 --oneline
```

If the tree already contains changes, inspect them. Preserve intentional user or prior-agent work. Do not reset, clean, stash, or overwrite changes you did not create. Determine whether they are the unfinished active slice and continue them when appropriate.

### B. Read the persistent ledger first

Read all of `docs/ENGINEERING_LOOP_STATE.md`, including:

- current contract and invariants;
- active slice, scope, non-goals, and acceptance criteria;
- current checks and latest evidence;
- open live gates;
- known risks and deferred debt;
- ordered next candidates.

If the ledger conflicts with code or test evidence, investigate and correct it in the same bounded slice. Keep the ledger concise enough to guide the next invocation; use append-only dated evidence sections or linked reports for detail.

### C. Read the current source of truth

Always inspect at least:

1. `README.md`
2. `STATUS.md`
3. `ARCHITECTURE.md`
4. `SECURITY_AND_SAFETY.md`
5. `docs/ENGINEERING_LOOP_STATE.md`
6. `docs/CONTINUOUS_PLANNING.md`
7. `pyproject.toml`
8. `config/default.yaml`
9. the active live profile, when relevant
10. `prompts/planner_system.md`
11. `src/kenshi_agent/models.py`
12. `src/kenshi_agent/runtime.py`
13. `src/kenshi_agent/continuous_executor.py`
14. `src/kenshi_agent/planning.py`
15. `src/kenshi_agent/world_state.py`
16. `src/kenshi_agent/safety.py`
17. `src/kenshi_agent/safety_supervisor.py`
18. `src/kenshi_agent/control_ownership.py`
19. `src/kenshi_agent/options.py`
20. `src/kenshi_agent/env/live.py`
21. `src/kenshi_agent/live_dev.py`
22. `src/kenshi_agent/overlay.py`
23. `src/kenshi_agent/skills/registry.py`
24. `src/kenshi_agent/food_procurement.py`
25. `src/kenshi_agent/native_commands.py`
26. all tests directly relevant to the selected slice.

For native or semantic-launch work, also read the entire native source, native
README, telemetry protocol, current upstream lock, causal-command ADR,
stable-identity ADR, live stability incident, live validation checklist,
installed RE_Kenshi settings, staged/installed DLL hashes, and the exact
rollback artifact.

For observation-budget work, trace the complete path from typed `Observation` through payload construction, hosted planner adapters, prompt assembly, logging, failure reporting, and provider token limits.

For input-boundary work, trace the complete path from executor preconditions through guard reservation, `LiveEnvironment.dispatch`, input lease acquisition, foreground restoration, primitive emission, receipt creation, and budget resolution.

### D. Run a baseline before editing

Use the repository’s existing virtual environment and supported tooling. Do not reinstall everything blindly when a healthy environment already exists.

At minimum, run the strongest available equivalent of:

```bash
python -m pytest -o addopts='' -q
ruff check .
mypy src
python -m compileall -q src scripts
kenshi-agent doctor --config config/default.yaml
```

Export every generated schema to a temporary directory and compare it byte-for-byte with `schemas/`.

Run the project’s deterministic single-step seed set. For changes to planning, execution, safety, telemetry, conditions, options, or metrics, also run the portable continuous proof and the focused event-driven tests.

If package installation is necessary, use the repository’s documented method. If `pip` is unavailable in the existing environment, prefer the project’s existing `uv` workflow rather than changing production code to accommodate the host.

When a dependency or platform prevents a gate, record the exact command and error. Do not convert “unavailable” into “passed.” Keep optional hosted-provider tests isolated so their absence cannot block unrelated core tests.

### E. Select exactly one slice

Choose the highest unmet item in the priority queue below unless:

- the ledger identifies a regression or active incident;
- the current working tree already contains a coherent unfinished slice;
- the user explicitly authorizes the pending supervised live gate;
- a correctness flaw must be fixed before the next live action.

Write the chosen problem, scope, non-goals, and measurable acceptance criteria into the ledger before editing.

## Current priority queue

Completed P0–P5 architecture is not a suggestion to reimplement it. Preserve it and repair only demonstrated regressions.

### P0 — Regressions, active incidents, and evidence integrity

This always comes first.

Examples:

- a current test, lint, type, schema, doctor, or deterministic seed regression;
- telemetry no longer advances or capability trust is inconsistent;
- the installed native binary/hash differs from recorded evidence;
- a run artifact, config, prompt, and implementation disagree about control or planning mode;
- a current live session is not in a confirmed safe state;
- the working tree contains an unfinished higher-priority slice.

Do not widen the scope while a safety or evidence invariant is broken.

#### Current P0 gate — Finish protocol 0.5 interruption, resolution, and ownership proof

The first 1920x1080 semantic startup half of this gate is complete. The
remaining tests are separate live boundaries; choose one only after the user
has explicitly said the computer is clear. Until then, keep Kenshi stopped and
choose the strongest offline slice.

Required sequence:

1. Confirm Kenshi is stopped, the current commit/tree is understood, the
   original `0.5.0` and replaced `0.4.0` rollbacks both exist, the current
   installed split title/player build hash is
   `33e54224f4b4729ba5b96c85db8b8f81137b5e153a7a97b3d4b8125813a89a7c`,
   its complete preinstall rollback is
   `runs/p0-title-player-split-preinstall-20260723T231348Z/`,
   `OpenSettingOnStart` is false, and all intended reduced graphics settings
   persisted.
2. Preserve the accepted candidate and its rollback. Never reinstall either
   rejected MyGUI-integrated DLL. Before any future replacement, back up the
   complete currently installed package and verify hashes while Kenshi is
   stopped.
3. Treat the recorded 1920x1080 no-Continue canary and semantic load-to-pause
   as accepted evidence unless a later change invalidates it. Do not repeat it
   merely for confidence.
4. Keep the exact live record: unique `CONTINUE` bounds, title sequences
   28→46→134, loaded sequences 36→245, persisted settings, flat title memory,
   settling loaded memory, unavailable GPU-local counter, clean logs, zero
   gameplay commands, and normal closes.
5. If a future run changes any startup/native/settings code or installed
   binary, re-open only the affected part of this accepted evidence.
6. Exercise launcher interruption deliberately once. New human input must
   permanently cancel the remaining startup sequence, never retry focus, and
   send at most one coordinate-independent safety pause only when fresh
   telemetry proves a loaded unpaused game.
7. At one alternate client resolution, repeat the semantic startup boundary.
   Legacy calibrated gameplay pointer skills must remain blocked; only
   semantic startup controls and coordinate-independent keys may act. Restore
   the intended profile afterward and verify it persisted.
8. In a separately bounded safe continuous run, exercise
   `human_control → takeover_pending → agent_active`, one countdown reset from
   new input, and F12 `disarmed`. Prove the cancelled plan never resumes and
   any post-countdown planner call starts from a newer validated paused
   revision.
9. Inspect `kenshi.log`, RE_Kenshi/plugin logs, Windows Application events,
   renderer/device-reset evidence, and the BAD STUFF surface before closing the
   gate.

Acceptance criteria:

- No startup pointer coordinate is hard-coded or inferred from resolution.
- Duplicate, missing, stale, changed, disabled, or hidden semantic targets
  produce zero pointer input.
- Fresh native plug-in `error` state, `RE_Kenshi Crash Reporter`, or
  `Kenshi has crashed` title terminates the launcher immediately with no
  additional input.
- 1920x1080 and the chosen alternate resolution both reach the expected title/
  save transition from current semantic bounds; this proves the startup path,
  not general resolution support for gameplay macros.
- Human interruption never enters a focus-reclaim loop.
- Ownership transitions and countdown seconds are visible in the external
  capture-excluded overlay and append-only log.
- Any new input resets pending takeover; F12 keeps the run disarmed; successful
  takeover replans current state rather than resuming cancelled work.
- The loaded game is paused at every terminal boundary where pause is
  authoritative.
- The reduced graphics profile shows no immediate monotonic memory rise or
  renderer reset during the bounded smoke. Long-duration stability remains
  explicitly open.

### P1 — Complete the supervised P6 live continuous food chain

Choose this only when the user explicitly authorizes supervised live input for the current invocation and every current dry, build, configuration, and telemetry gate is green.

The target is the existing calibrated Barman proof, not a generic commerce system.

Required sequence:

1. Confirm exact commit, clean/understood tree, config hash, control mode, planning mode, policy version, game/plugin version, DLL hash, resolution, UI scale, window mode, keymap/calibration identity, save/mod context, fresh advancing telemetry, selected character, money, food count, and confirmed pause.
2. Reconcile the prior partial live plan honestly: one accepted approach step
   executed and moved closer, but the plan aborted on a human-input event before
   dialogue/trade/inspection/purchase. Do not count that as chain completion or
   automatically reuse its command/plan/target/session identity.
3. Run one final **zero-input hosted preflight** against the current protocol and policy. A rejected or stale response must execute nothing.
4. Before any real input, state the exact proposed live chain and receive/confirm explicit authorization if it is not already unambiguous in the surrounding request.
5. Execute the world/dialogue/inspection phase from one accepted strategic `PlanEnvelope` where policy permits.
6. Require exact stable target, role, selection, dialogue target/text, trade owner, tooltip source, food marker, item name, expected price, balance, capability set, and current phase before each sensitive action.
7. Use a separate later grounded response for the at-most-once purchase when required by policy.
8. Verify exact debit, food-count increase, final pause, command IDs, action count, strategic-call count, plan lifecycle, and remaining budgets on causally later revisions.
9. Exercise one safe preemption path—F12 or deliberate human input—only if the live checklist and user authorization include it. Confirm one idempotent cleanup and no repeated action.
10. Inspect application, plugin, renderer, telemetry, and run logs before declaring completion.

Acceptance criteria:

- At least one accepted live `PlanEnvelope` executes multiple already-approved steps without a model call between every action.
- Every state-changing receipt has later causal evidence or an explicit inconclusive/rejected result.
- The purchase occurs at most once and only after exact current tooltip and spending checks.
- Any selection, target, role, UI, dialogue, trade ownership, tooltip, price, balance, capability, revision-fence, or calibration mismatch prevents the sensitive action.
- The game ends in a confirmed paused state.
- Run artifacts record exact provenance and separate native-assisted evidence from interface-only evidence.
- Documentation says only that this exact calibrated flow was proven.

If live authorization is absent, do not simulate having completed this gate. Select P2 instead unless the ledger identifies a more urgent local regression.

### P2 — Replace planner-payload string slicing with semantic valid-JSON budgeting

This is the default next local engineering slice when P1 cannot be performed.

Problem: the current payload budgeter can cut serialized JSON inside a string or object. That can send malformed evidence to the hosted planner and makes truncation behavior dependent on incidental serialization order.

Required design:

- Build a deterministic semantic budgeter over structured data, not a substring operation over serialized text.
- Always return parseable JSON within `max_observation_chars`, or fail explicitly when even the irreducible safety envelope cannot fit.
- Preserve the full fidelity of critical fields before low-priority context.
- Drop whole fields, collection elements, history entries, or optional payload sections.
- Include explicit omission/truncation metadata and original/retained counts.
- Never truncate stable IDs, command IDs, plan IDs, enum values, numeric safety values, or condition operands into partial strings.
- Keep provider prompts, logs, tests, and docs consistent with the new contract.

Priority order:

1. control mode, planning mode, safety state, freshness, and exact world revision;
2. active plan ID/version/step, remaining budgets, command state, and last causal outcome;
3. selected character and exact entities referenced by active/proposed work;
4. relevant capabilities, game state, UI state, dialogue/trade/tooltip evidence;
5. deltas and transient events since the planner’s prior accepted revision;
6. currently available actions/options and their machine-enforced constraints;
7. bounded outcome history and memories;
8. unrelated nearby entities and low-priority context.

Acceptance criteria:

- An oversized observation satisfies `json.loads(payload)` for every tested budget at or above the documented irreducible minimum.
- `len(payload) <= max_chars` is enforced deterministically.
- Critical safety/plan/target fields survive while lower-priority context is omitted.
- Omission metadata is itself valid, bounded, and truthful.
- Reordering unrelated low-priority source items does not unpredictably evict critical data.
- Property/fuzz-style tests cover nested strings, Unicode, long rationales, large entity lists, histories, events, and very small budgets.
- Hosted planner adapters receive the same valid structured contract.
- No single-step, continuous, schema, or deterministic seed regression occurs.

Do not combine this slice with a large runtime refactor.

### P3 — Add a final post-lease pre-input execution fence

Problem: executor/guard validation may occur before `LiveEnvironment` waits for a polite input turn. The relevant UI or world state can change while the lease is pending.

Current boundary: semantic developer-startup clicks already re-read the exact
unique label and bounds inside the lease, and every pointer-bearing live action
rechecks expected client width/height there. Preserve those proven local
fences. This slice must generalize the missing typed plan/step/target/UI
authority for gameplay dispatch rather than replacing the semantic launcher
with another framework.

Required design:

- Introduce a bounded execution token, callback, or equivalent mechanism carried from executor validation into live dispatch.
- After the input lease is acquired and foreground/cursor prerequisites are established—but immediately before the first primitive—read the latest canonical observation/revision.
- Re-evaluate the step’s required capabilities, protected plan assumptions, step preconditions, exact target/selection/UI evidence, control mode, calibration identity, and remaining authorization.
- Prevent input when the lease wait crossed a relevant state change or the revision can no longer be reconciled under the current policy.
- Resolve reservations correctly: release proven non-dispatch, preserve conservative treatment after ambiguous partial dispatch, and never double-spend an at-most-once action.
- Log a distinct boundary-revalidation outcome, revision, wait duration, and reason.
- Preserve polite human handoff and existing F12 behavior.

Acceptance criteria:

- A deterministic test blocks inside a fake input lease, publishes a conflicting state, releases the lease, and proves zero primitives were emitted.
- An unchanged acceptable state executes exactly once.
- Capability withdrawal, target change, selection change, UI phase change, calibration change, human activity, and emergency stop each prevent dispatch appropriately.
- A native-assisted command retains its stronger DLL fence and does not lose command-ID/acknowledgement semantics.
- The final boundary check uses the same typed condition machinery rather than an unrelated ad hoc boolean path.
- Receipts and metrics distinguish pre-lease rejection, post-lease rejection, issued, rejected, completed, and inconclusive.

Do not shorten or disable the polite lease to make the test pass.

### P4 — Make calibration identity a hard live pointer-action gate

Problem: live pointer execution now universally requires configured client
width/height and rechecks it inside the lease, but this is only an emergency
calibration brake. It is not a complete versioned calibration identity and
must not become the architecture for genuinely semantic UI anchors.

Required design:

- Define a stable calibration identity or fingerprint that covers every fact needed for pointer validity, such as client dimensions, window mode, resolution, UI scale, DPI/client transform, keymap where relevant, profile version, and calibrated macro set/hash.
- Expose current observed identity separately from expected profile identity.
- Mark calibration unknown or mismatched rather than inventing defaults.
- Require exact acceptable identity before any calibrated pointer action.
- Include calibration identity in observations, plan assumptions when relevant, execution tokens, receipts, run headers, overlays, summaries, and supervised evidence.
- Do not require calibration for actions that are genuinely coordinate-independent; document the distinction.
- Classify each pointer action as semantic-current, profile-calibrated, or
  unsupported. A current semantic control/tooltip/entity bound may remain
  resolution-independent only when its exact live evidence is revalidated
  inside the input lease.

Acceptance criteria:

- Matching identity allows the ordinary guarded path.
- Resolution, client size, UI scale, DPI transform, window mode, profile hash, or missing identity blocks calibrated pointer input before dispatch.
- A calibration change during an input lease is caught by P3’s final fence when P3 is present; otherwise record that dependency explicitly.
- Interface-only and native-assisted evidence both state calibration mode.
- No claim of general resolution support is made from one profile.
- Startup semantic-control evidence at two resolutions does not waive
  calibration for legacy dialogue/trade/map/world macros.

### P5 — Unify final safe-state behavior across every terminal path

Problem: movement and supervisor cleanup have strong pause guarantees, but ordinary `StopAction`, max-step termination, planner failure, environment exception, cancellation, close, and process shutdown do not yet share one universal terminal protocol.

Required design:

- Define one idempotent `ensure_final_safe_state`-style protocol owned by deterministic runtime/safety code.
- Use the narrowest available guarded pause path.
- Require a causally later capable paused revision before reporting success.
- Distinguish already-safe, newly-paused, unavailable, timed-out, failed, and inconclusive outcomes.
- Ensure terminal cleanup cannot accidentally resume the game.
- Prevent duplicate cleanup across executor, supervisor, runtime exception handlers, and `close()`.
- Put final-safe-state status and evidence in summaries and terminal events.

Acceptance criteria:

- Tests cover normal completion, explicit stop, max-step termination, planner exception, environment exception, task cancellation, supervisor preemption, missing pause capability, stalled telemetry, repeated close, and cleanup failure.
- Exactly one terminal cleanup owner acts.
- No successful run summary claims a confirmed safe state without later evidence.
- Existing movement cancellation and supervisor cleanup behavior remains compatible.

### P6 — Create a live option long enough for strategic thinking to matter

After P1–P5 are proven or consciously gated, make live planning feel continuous rather than merely multi-step.

The current short movement pulse ends before a roughly 24-second hosted response is useful. Do not re-enable concurrent advice merely to report nonzero overlap.

Build one monitored, bounded option with a naturally longer execution window. A travel/approach option is preferred over combat or commerce expansion.

A suitable option may:

- issue one bounded destination or exact target intention;
- remain active for tens of seconds while telemetry advances;
- expose progress, distance, movement/task state, target lifetime, selection, threat, pause, and timeout predicates;
- request or accept a future-only strategic patch while continuing;
- cancel immediately on human input, emergency stop, stale/stalled telemetry, capability loss, target loss, threat policy, or plan invalidation;
- end with a confirmed safe pause when its contract requires it.

Acceptance criteria:

- The option’s expected useful duration exceeds the measured median planner latency under the chosen provider profile, or a faster tactical/strategic tier is explicitly introduced and measured.
- Observation continues while the option runs.
- A strategic call starts from immutable revision `R` while the option remains active.
- At least one returned advisory/patch is either accepted after current-state revalidation or correctly discarded as stale; both paths have deterministic tests.
- The planner cannot mutate the running step or already-executed history.
- User input and safety preemption remain prompt and idempotent.
- Metrics report overlap duration/fraction, option progress, patch latency, accepted/stale advisories, planner calls saved, and final safe state.
- A supervised live claim is made only after a separately authorized exact run.

Consider a two-speed architecture if measurement supports it:

- a slower hosted strategic planner for objectives and future branches;
- deterministic or low-latency typed tactical logic for option polling and immediate reactions.

Do not let the tactical layer silently become an unreviewed second strategic planner.

### P7 — Expand AI-to-player-character affordances from observation outward

Only after the continuous substrate is live-proven and hardened, expand breadth in this order unless current evidence supports a better dependency order:

1. exact selection set, active task/order, order cancellation, and focus;
2. hunger, health, body-part condition, bleeding, unconsciousness, and danger state;
3. medical aid, carrying, rescue, beds, and recovery;
4. generic inventory, stack, equipment, food consumption, and transfers;
5. generalized trade dialogue, inventory grids, buying, selling, and affordability;
6. combat threat, attack target, stance, retreat, and tactical control;
7. jobs, workstations, storage, production, and hauling;
8. building, crafting, research, and base management;
9. recruitment, squad formation, roles, and multi-character coordination.

For every affordance, complete the whole ladder:

```text
validated observation source
→ nullable typed state
→ capability contract
→ stable identity/lifetime
→ planner-visible representation
→ typed conditions
→ bounded option/action
→ safety policy
→ causal postcondition verifier
→ deterministic transition tests
→ supervised live evidence
```

A new click macro is not a completed affordance when its preconditions, target, or result cannot be authoritatively observed.

Prefer affordances that unlock survival and recovery loops before spectacle-only actions. Do not generalize from one trader, one HUD, one save, or one resolution.

### P8 — Operational and quality maturity

Address these as bounded slices after higher correctness gates, or earlier when they block safe iteration:

- Linux and Windows CI with a dependency-free core lane and opt-in provider/native lanes;
- reproducible Python lockfile and documented update process;
- optional hosted-provider tests isolated with narrow skips;
- deterministic fake Win32/controller/input-lease harness;
- property/state-machine tests for plan, option, safety, budget, and terminal invariants;
- mutation tests for critical safety predicates where practical;
- generated current capability/action/skill/config documentation;
- configuration-use audit for declared fields;
- measured complexity refactors of `runtime.py`, `continuous_executor.py`, and `world_state.py` only when behavior remains covered;
- structured run/evidence manifest generation;
- longer native/renderer stability soaks and fault-injection tests.

Do not use quality work as an excuse to postpone the pending live milestone indefinitely, but do not perform the live milestone on a broken baseline.

## Required continuous-planning semantics

### The executor owns real-time plan state

The executor, not the model, tracks:

- current plan ID and version;
- protected executed/current step IDs;
- active step and option lifecycle;
- remaining action, wall-clock, game-time, pointer, native, purchase, and other risk budgets;
- retries and idempotency state;
- pending command/acknowledgement;
- current world revision and action-start revision;
- cancellation reason;
- success/failure branch;
- final safe-state status.

Before every step:

1. read the latest canonical revision;
2. verify plan assumptions, capabilities, calibration, and typed step preconditions;
3. validate the action against control mode, policy, allowlists, pointer envelopes, and remaining budgets;
4. reserve relevant budgets;
5. acquire any needed input/native execution authority;
6. perform the final boundary revalidation at the actual issue point;
7. start the action or option;
8. commit, release, or quarantine reservations according to proven outcome;
9. evaluate success/failure only on later revisions;
10. branch, complete, abort, or request a future patch.

### Strategic planning may overlap execution

The strategic planner may receive an immutable snapshot at revision `R` while a currently authorized option continues. It may return a future plan or patch. The scheduler must compare the response’s basis, plan version, protected steps, assumptions, targets, capabilities, and remaining budgets with latest state before accepting it.

A late response is advisory, not executable authority.

### Preserve the narrow food-policy rebase boundary

Generic strategic output must match the current exact revision unless a separately reviewed policy defines a narrower safe rebase.

For `food_procurement_v1`, preserve the existing sequence-only rebase semantics:

- the returned plan basis must match its immutable planner snapshot;
- current state must be causally later;
- identity session, capabilities, game/UI phase, native command state, selected character, and exact vendor fence must remain equivalent;
- any relevant changed value rejects the rebase;
- rebase changes only the basis and does not skip policy, assumption, precondition, guard, budget, calibration, or input-boundary validation.

Do not generalize this exception to arbitrary plans without a separate ADR, threat analysis, and tests.

### Use optimistic concurrency for patches

A `PlanPatch` may alter only future steps. Reject it when:

- plan ID/version differs;
- current or executed step is changed;
- protected IDs are removed or reused;
- assumptions or target lifetimes changed;
- budgets no longer cover the replacement;
- the response became stale under current policy;
- its branch graph is invalid or unreachable.

A rejected stale patch is a normal lifecycle result and metric, not necessarily a planner crash.

### Keep a deterministic fast layer

High-frequency behavior must not require the hosted model. Deterministic code may:

- poll a bounded option;
- evaluate typed predicates;
- preserve an auto-pause;
- monitor progress and timeout;
- yield to human input;
- stop on threat, staleness, capability loss, or target loss;
- request a strategic replan.

Do not allow the fast layer to invent broad goals, unbounded retries, or new action classes outside reviewed policy.

### Treat ownership transfer as scheduler state

When automatic takeover is configured, human input is not a terminal error and
is not merely an idle timer:

1. cancel the planner/executor and make any active command inconclusive or
   terminal according to its actual evidence;
2. use the independent safety path to establish or verify pause;
3. stop the latched safety supervisor and publish `human_control`;
4. after the configured quiet interval, publish `takeover_pending` and every
   visible countdown second;
5. reset to `human_control` on any new input and enter `disarmed` on F12;
6. at zero, require a fresh later loaded/paused/capable revision, unchanged
   control mode, no active command, remaining run authority, and applicable
   calibration;
7. start a new safety supervisor and request a fresh plan. Never reactivate the
   cancelled plan.

The overlay is observational and capture-excluded. Closing it must not change
ownership state, but a configured live run must still log and print every
transition.

### Log the complete lifecycle

Maintain and test events equivalent to:

```text
plan_proposed
plan_accepted
plan_rebased
plan_rejected
plan_started
plan_step_ready
plan_step_started
plan_step_progress
plan_step_succeeded
plan_step_failed
plan_step_cancelled
plan_patch_requested
plan_patch_staged
plan_patched
plan_patch_rejected
option_prepared
option_started
option_progress
option_succeeded
option_failed
option_cancelled
budget_reserved
budget_committed
budget_released
budget_quarantined
input_lease_wait_started
input_lease_acquired
input_boundary_revalidated
input_boundary_rejected
command_issued
command_acknowledged
command_completed
command_inconclusive
plan_completed
plan_aborted
safety_preempted
control_ownership_changed
agent_takeover_countdown
agent_takeover_cancelled
agent_takeover_ready
final_safe_state_requested
final_safe_state_confirmed
final_safe_state_failed
```

Every event needs the relevant plan ID/version, step/option/command ID, world revision, control mode, policy/calibration identity, reason, and bounded evidence. Replay must reconstruct the same terminal plan, option, budget, and safe-state status.

## Testing requirements

### Unit tests

Cover:

- strict schemas and generated-schema parity;
- plan graph and patch validation;
- condition tri-state/five-state evaluation;
- food-policy phase and rebase fences;
- semantic observation budgeting;
- transactional budget accounting;
- execution-token/input-boundary validation;
- calibration matching;
- terminal safe-state ownership;
- lifecycle serialization and replay;
- provider-specific structured-output compatibility in isolated optional tests.

### Deterministic event-driven tests

Use fake clocks, scripted state streams, fake input leases, and controlled planner futures. Cover:

- two or more actions from one plan;
- precondition changes before a future step;
- old-but-wall-clock-fresh state;
- planner latency with unchanged sequence-only food fence;
- planner latency with one changed fenced value;
- stalled telemetry sequence;
- command ID mismatch and old acknowledgement;
- stable identity reordering, disappearance, and generation change;
- capability withdrawal;
- planner blocked while safety preempts;
- user interruption during movement and during input-lease wait;
- human-control quiet interval, every countdown transition, reset by new input,
  F12 disarm, post-countdown revalidation failure, and fresh replan success;
- cancellation of a running plan followed by takeover without restarting its
  executed/current step;
- semantic startup control at two different client sizes, duplicate/missing
  labels, bounds changing inside the lease, and permanent launcher interruption;
- cancellation before, during, and after primitive dispatch;
- post-lease state change with zero emitted input;
- cleanup failure and retry policy;
- branch, retry, timeout, and every budget exhaustion path;
- stale patch rejection and valid future-only patch acceptance;
- semantic payloads across large and tiny budgets;
- calibration mismatch at pre-dispatch and post-lease boundaries;
- single-step compatibility;
- terminal safe state on every exit path.

### Property and state-machine invariants

At minimum, assert:

- no step executes before its preconditions are acceptable on the actual issue revision;
- no plan exceeds action/time/risk budgets;
- completed or cancelled steps do not restart without an explicit new version;
- interface-only mode never emits a native command;
- a movement/travel option eventually reaches one terminal state;
- cleanup does not report success without later pause evidence;
- a purchase is never issued or committed more than once;
- every state-changing action has a causal receipt or explicit inconclusive/failure result;
- no stale patch mutates current or executed work;
- no malformed JSON payload reaches a planner adapter;
- no calibrated pointer action executes under unknown/mismatched identity;
- no human-input lease wait bypasses final preconditions;
- quiet time never returns control without a completed visible takeover
  countdown and current-state revalidation;
- a cancelled pre-handoff plan never resumes after takeover;
- F12-disarmed ownership never automatically becomes agent-active;
- no semantic click executes from missing, duplicate, stale, or changed
  label/bounds evidence;
- exactly one component owns terminal cleanup.

### Platform and supervised live tests

Keep portable, Windows, native, and live evidence separate.

For every supervised run, record:

- exact commit and dirty-tree status;
- config and prompt hash;
- control and planning modes;
- live policy version;
- Kenshi, RE_Kenshi, and plugin versions/hashes;
- resolution, client size, UI scale, DPI, window mode, keymap, and calibration identity;
- save/mod context and starting selected character;
- start/end telemetry revisions and identity session;
- exact command/action/plan IDs;
- screenshots and log paths;
- operator authorization and interventions;
- expected and observed outcomes;
- money/inventory/task/position deltas where relevant;
- planner latency and strategic-call count;
- whether final safe state was causally confirmed;
- application/plugin/renderer/system-log inspection result.
- ownership-state/countdown/reset/disarm events and whether the overlay was
  visible and capture-excluded;
- semantic control label/role/bounds at each startup issue point and whether
  the action was semantic-current or profile-calibrated.

Never let one supervised proof silently become a generic claim.

## Metrics to maintain

### Causality and responsiveness

- observation age at plan acceptance, option start, and actual input issue;
- telemetry sequence lag;
- input-lease wait duration;
- pre-lease to post-lease revision delta;
- command-to-ack and ack-to-postcondition latency;
- percentage of receipts with later post-command revisions;
- sequence-stall and regression incidents;
- transient-event retention/loss;
- final-safe-state request-to-confirm latency.

### Planning and execution

- strategic calls;
- actions and completed steps per strategic call;
- plan completion, abort, timeout, policy rejection, and stale rejection rates;
- plan patches requested, accepted, rebased, and discarded;
- average chain length;
- retries, cancellations, and inconclusive dispatches;
- option success/failure/cancel/cleanup rates;
- fraction of execution wall time overlapped by strategic planning;
- percentage of model output discarded as stale;
- planner calls saved by deterministic chain execution.

### Game-time and wall-time efficiency

- wall-clock-to-game-time ratio;
- pause duty cycle by reason;
- planner duty cycle;
- input-lease waiting duty cycle;
- progress per wall-clock minute;
- no-op/stagnation rate;
- recovery time after failed precondition, target loss, or replan;
- useful option duration versus planner latency.

### Safety and human control

- preemptions by cause;
- human-interruption count and yield latency;
- time in `agent_active`, `human_control`, `takeover_pending`, and `disarmed`;
- takeover countdowns started, reset, completed, revalidation-rejected, and
  disarmed;
- time from human input to confirmed pause and from countdown completion to
  fresh replanning;
- emergency-stop latency;
- unexpected unpaused duration;
- post-lease revalidation rejections;
- calibration mismatches;
- final confirmed-safe-state rate;
- budget reserve/commit/release/quarantine counts.

### Affordance quality

For each action or option:

- precondition result distribution;
- accepted, issued, acknowledged, executed, and verified rates;
- false-success, inconclusive, and mismatch rates;
- target lifetime/selection mismatch;
- retries and recovery;
- evidence count by control mode, policy, and calibration identity.

### Model value and cost

- latency, tokens, and cost per strategic call;
- cost per in-game minute and successful subgoal;
- output validity and policy acceptance by model/reasoning effort;
- actions/steps saved per strategic call;
- overlap utility: how often planning completes before the running option ends;
- quality/latency comparison across hosted or local tiers when measured.

Do not optimize “actions per planner call” in isolation. A long open-loop chain with weak verification is a regression.

## Documentation and evidence discipline

Use these categories explicitly:

- **Current contract:** generated from or reviewed against current code/config.
- **Automated portable evidence:** tests and deterministic simulations.
- **Windows integration evidence:** input/controller/launcher behavior.
- **Native build/load evidence:** exact binary and protocol behavior.
- **Supervised live evidence:** one exact Kenshi run.
- **Historical report:** dated and not presumed current.
- **Proposed design:** not yet implemented.

Update current docs in the same slice as behavior. Do not rewrite historical evidence to make it look current. Add or revise ADRs for consequential changes such as input-boundary authority, calibration identity, terminal safe-state ownership, or generalized stale-plan rebase policy.

Prefer generated capability, action, condition-path, skill, config-default, and schema documentation where practical.

Keep `docs/ENGINEERING_LOOP_STATE.md` as the current handoff, not an unbounded narrative. Preserve exact live evidence in dated reports/checklists or an append-only evidence section.

## Per-invocation implementation method

1. **Establish state.** Inspect git, ledger, current checks, active incidents, and live authorization boundary.
2. **Choose one slice.** Record problem, scope, non-goals, and acceptance criteria in the ledger.
3. **Write failing tests first.** Include at least one failure path and one safety/cancellation/unknown-state path for behavior changes.
4. **Implement the smallest complete design.** Reuse current boundaries; do not create a parallel unintegrated framework.
5. **Run focused tests continuously.** Keep failures attributable.
6. **Run full available gates.** Tests, Ruff, mypy, compile, schema comparison, doctor, fixed seeds, and relevant continuous proofs.
7. **Inspect the diff.** Remove secrets, run artifacts, temporary payloads, generated binaries, and unrelated rewrites.
8. **Update schemas, config, prompts, docs, and ledger.** State implemented versus proposed behavior and exact evidence.
9. **Report honestly.** No Windows/native/live claim without the matching run.
10. **Stop when the slice is complete.** Do not begin the next item in the same invocation unless it is inseparable from acceptance.

When a slice reveals a deeper flaw, do not hide it behind compatibility code. Either solve it within the declared scope or record a precise next item with the failing invariant.

## Milestone definitions

### Portable continuous foundation

Treat this milestone as already implemented unless current tests prove a regression. It requires:

- `single_step` remains green;
- strict bounded plans and patches exist;
- one strategic call can execute at least two actions;
- conditions are typed and capability-aware;
- postconditions require later revisions;
- stale future actions and patches are rejected;
- safety can preempt a blocked planner;
- a stateful movement option can overlap a future advisory in deterministic tests;
- cancellation produces verified safe pause where required;
- lifecycle replay and metrics agree.

Do not spend an invocation rebuilding this milestone from scratch.

### First supervised live continuous-chain milestone

This milestone is complete only when:

- protocol `0.5.0` has first passed the separate semantic-startup, graphics,
  stability, and ownership P0 gate;
- the exact current `food_procurement_v1` live policy is explicitly authorized;
- a zero-input hosted preflight is green;
- one accepted live strategic response drives multiple guarded world/dialogue/inspection steps without a model call between each step;
- every sensitive action is checked against the latest permitted revision and exact target/UI evidence;
- the separately grounded purchase is issued at most once;
- exact later money and food deltas prove the outcome;
- all plan/command/budget lifecycle events reconcile;
- one tested preemption path remains safe when authorized;
- final paused state is causally confirmed;
- evidence names the exact native-assisted and calibration boundary;
- no generic Kenshi/trader/resolution claim is made.

The prior accepted approach command is useful partial evidence, but one
completed approach followed by an aborted plan does not satisfy this
milestone.

### First meaningful live planner-and-thinker milestone

This is the user’s primary qualitative goal. It is complete only when:

- a live monitored option remains active long enough for strategic computation to be useful;
- observations and safety continue independently throughout the option;
- the strategic planner receives immutable revision `R` while execution continues;
- the result is accepted only after current-state, plan-version, target, capability, calibration, and budget revalidation;
- a stale or irrelevant result is safely discarded without stopping valid current work;
- the accepted future plan or patch changes subsequent behavior without modifying the running or executed step;
- human input or safety preemption cancels promptly and idempotently;
- the option ends in its documented safe state;
- metrics show real overlap, planner calls saved, patch utility, causal latency, and no weakened safety.

A model response that arrives after a two-second pulse has already ended does not satisfy this milestone, even when the code technically launched the tasks concurrently.

## Required final report for every invocation

Use this format:

```markdown
# Engineering Loop Result

## Slice completed
One sentence naming the bounded improvement.

## Why this was the right next slice
Reference current ledger state, any active gate, and the user’s live-agency goal.

## Changes
- File/path and behavioral change.
- File/path and behavioral change.

## Evidence
- Exact commands run.
- Test counts and results.
- Schema/config/prompt/doc consistency checks.
- Deterministic, Windows, native, hosted, or live evidence, clearly labeled.

## Metrics before → after
Only metrics actually measured.

## Safety and experiment-boundary review
State control mode impact, action surface, final input-boundary checks, cancellation/cleanup behavior, calibration impact, and any remaining ambiguity.

## Not tested
List platform, dependency, provider, native, API, or real-game limitations.

## Working-tree state
Summarize intentional remaining changes and artifacts removed.

## Ledger update
State the new current milestone and the next three ordered candidates.
```

Also update `docs/ENGINEERING_LOOP_STATE.md` with the same facts in compact persistent form.

## Stop conditions

Stop the invocation and leave a precise report rather than widening scope when:

- the selected slice is complete and green;
- a live step requires authorization not present in the current request;
- a focus-taking, input-injecting, resolution-changing, or ownership-handoff
  test lacks a current operator statement that the computer is clear;
- a product or experiment-boundary decision materially changes policy and is unresolved;
- a platform/dependency issue prevents the next safe implementation step;
- the current native/telemetry state is not trustworthy;
- unrelated pre-existing changes make target files unsafe to edit;
- continuing would require unapproved control of Kenshi or another sensitive context.

Do not stop merely because the problem is difficult. Produce the strongest complete local increment available and make the next step exact.

## Current emphasis

The project has crossed the architectural threshold from single-action deliberation to bounded continuous execution. The next objective is to turn that architecture into trustworthy live behavior rather than adding isolated macros.

Preserve this shape:

```text
continuous telemetry and event ingest
        ↓
versioned world-state store
        ↓
independent deterministic safety
        ↓
interruptible option/plan executor ← strict bounded plan or future patch
        ↑
asynchronous strategic planner
```

The immediate decision rule is:

- fix a current regression or safety incident first;
- preserve the accepted 1920x1080 protocol `0.5.0` semantic startup and
  reduced-graphics smoke;
- when the operator says the computer is clear, finish deliberate interruption,
  alternate-resolution startup, ownership countdown/reset/disarm, and the
  longer stability soak in separately bounded tests;
- only after P0 is green, and with explicit live-action authorization, finish
  the exact P6 Barman chain;
- without current live-test readiness, replace malformed planner-payload
  truncation with semantic valid-JSON budgeting;
- then close the ordinary gameplay post-input-lease evidence race, complete
  calibration identity beyond exact client size, and unify final safe-state
  behavior;
- then build a long-running monitored travel option so strategic thinking can overlap useful live execution;
- only after that broaden player-character affordances.

Begin now by establishing repository and ledger state, running the baseline, and selecting exactly one highest-priority bounded slice.
