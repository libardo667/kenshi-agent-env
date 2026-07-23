# Engineering loop state

## Current contract

- The portable `single_step` runtime is the regression baseline. It asks one
  planner for one action, passes that action through `ActionGuard`, executes it
  through the selected environment, and records the observed outcome.
- Every run declares `interface_only` or `native_assisted`.
  `interface_only` is the default and cannot advertise or execute a marked
  native-assisted skill. Native-assisted live execution requires its own config
  opt-in and CLI acknowledgement in addition to the normal live-action gates.
- Live keyboard and mouse input requires both
  `safety.live_actions_enabled: true` and `--execute-live-actions`.
- F12 checks, stale-telemetry gates, pointer envelopes, action/rate/purchase
  limits, polite human-input yielding, and movement re-pause are invariants.
- Missing telemetry remains unknown and capability-gated.
- `single_step` remains the default. Feature-flagged `continuous` accepts a
  strict bounded `PlanEnvelope`; live-labeled environments terminate before a
  strategic call unless the explicitly configured versioned policy accepts the
  exact plan.
- Continuous mode owns one observation pump and bounded world-state store.
  Postcondition waits, planner snapshots, command receipts, deltas/events,
  entity lifetimes, and future subscribers share its canonical revisions.
- Portable continuous mode owns an independent deterministic safety subscriber
  that can cancel blocked planner/executor work and report safe cleanup only
  after a causally later capable revision confirms pause.
- Configured movement-pulse skills become stateful options in portable
  continuous mode. One future-only strategic patch may overlap movement, but
  it cannot execute until post-option state and remaining budgets are
  revalidated.
- Native protocol `0.5.0` retains process/session-scoped opaque entity IDs and
  the exact caller/revision/session/selection/target command envelope with
  bounded keyed lifecycle acknowledgements. It retains game time, exact
  dialogue, and current tooltip/source observations and adds bounded visible
  MyGUI control labels/roles/bounds.

## Active slice: P0 semantic launch and explicit control ownership

Problem: the previously mitigated Intel Iris Xe live profile reproduced
Kenshi's `BAD STUFF` out-of-video-memory crash after roughly forty minutes.
The captured dialog and prior incident evidence again point to a DirectX device
reset under shared-memory pressure. The installed configuration had not rolled
back: Low textures, disabled reflections and shadows, and disabled fast zone
hopping all persisted. Broad live stability is therefore open again and takes
precedence over another P6 action.

Scope:

- Preserve the crash evidence and the pre-restart configuration.
- Keep the existing Low-texture/reflection/shadow/zone-hopping mitigations,
  and reduce view distance from approximately 4000 to 2500.
- Relaunch without gameplay input, confirm a fresh loaded paused state and
  advancing protocol `0.5.0` telemetry, then measure Kenshi private memory,
  GPU-local usage, and host headroom while the client settles.
- Remove pixel coordinates from the native video-launcher transition and
  disable the optional RE_Kenshi startup panel through its durable setting.
- Export bounded, current MyGUI button labels and bounds so title-menu startup
  can target a semantic visible control instead of a resolution-specific
  coordinate. Keep exact client-size rejection only around legacy calibrated
  gameplay pointer actions until each one has its own semantic anchor.
- Make the developer launcher yield permanently on new human input, use an
  input lease, restore foreground/cursor, and remove automatic focus-taking
  click retries and the ungrounded post-load click.
- Replace silent idle-based reacquisition during a live continuous run with an
  explicit ownership lifecycle: `agent_active`, `human_control`,
  `takeover_pending`, and terminal `disarmed`. Human input cancels or resets a
  visible countdown; F12 disarms automatic takeover; a completed countdown
  still must pass fresh paused telemetry and control-mode checks before a new
  plan may be requested.
- If the reduced profile is stable, make the proven profile/checks durable in
  launcher-side Python/configuration rather than adding DirectX hooks to the
  native telemetry plug-in.

Non-goals:

- No DirectX interception, renderer patch, graphics-driver modification, or
  new native action surface.
- No direct invocation of MyGUI callbacks; semantic bounds remain read-only
  telemetry and ordinary keyboard/mouse input remains the action surface.
- No claim of broad or long-duration stability from one short verification.
- No dialogue, trade, purchase, or other gameplay input during the stability
  check.
- No resumption of the P6 food chain unless the reduced launch is fresh,
  paused, advancing, and below the immediate memory-pressure boundary.

Acceptance criteria:

- Installed graphics settings exactly match the intended reversible profile
  after relaunch.
- Kenshi reaches a loaded, causally confirmed paused state with fresh advancing
  telemetry and the expected installed DLL/protocol.
- The launcher emits no fixed pointer input for the video launcher or title
  menu. Ordinary live pointer skills still emit zero input when their required
  calibration identity does not match.
- Human input before or during a launcher input lease terminates launcher
  automation with no further input; startup never retries focus-taking clicks.
- Live human input produces a visible/logged `human_control` state. Automatic
  takeover is impossible before a resettable countdown completes, F12 keeps it
  disarmed, and resumption replans from a newly validated current revision.
- Settled memory samples show no immediate monotonic rise or device-reset
  symptom, and the evidence records any remaining headroom qualification.
- The crash incident document and current ledger distinguish proven mitigation,
  short validation, and still-unproven long-duration stability.

Implementation status: semantic launch and explicit ownership are implemented.
The first supervised 1920x1080 protocol `0.5.0` smoke loaded the original
185,344-byte candidate
(`a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`).
Kenshi reached a responsive title screen with no RE_Kenshi panel, and the
plug-in reported fresh `ready` state, but telemetry retained the prior session.
The launcher timed out waiting for a semantic Continue control and emitted zero
title clicks. This failed closed and exposed a lifecycle dependency:
`PlayerInterface::update` does not run before a save creates the player.
The hotfix samples after MyGUI's per-frame title/game update and leaves loaded-
game native-command monitoring on `PlayerInterface::update`. It compiled under
the pinned VS2010 SP1 toolchain as a 186,368-byte DLL with SHA-256
`ace964357eaa93c8844d1b564447bf85650dba97434f67f7875cdb03f1de88d5`;
installation and repeat smoke remain pending at this checkpoint. The replaced
protocol `0.4.0` DLL is preserved under
`runs/p0-semantic-launch-preinstall-20260723T2208Z/installed-plugin-backup/`.
The frozen process and `BAD STUFF`
dialog were preserved before shutdown. Pre-restart configuration is retained
under the associated run directory. A 1280x720 trial reached fresh advancing
telemetry, but the user observed misaligned startup clicks and could not regain
focus because the launcher retried with polite input disabled. That trial is
rejected as a control-safety result. Kenshi was stopped, the renderer was
restored to calibrated 1920x1080, and no gameplay action had been dispatched.
The installed profile retains view distance `2500`, Low textures, disabled
water reflections/shadows, and disabled fast zone hopping. The launcher now
uses input leases, latches new human input as terminal, makes one startup
sequence without click retries, uses a coordinate-independent pause key, and
requires a causally confirmed paused result. The live environment rechecks the
exact calibrated client size inside the acquired input lease before every
pointer-bearing action. Full portable evidence before the lifecycle hotfix is
209 passing tests, Ruff, mypy across 48 source files, compile checks, schema
parity, default doctor,
three fixed single-step seeds, and the continuous mock proof.

## Pending live milestone: P6 conditional food-procurement chain

Problem: P1-P5 can execute and causally acknowledge bounded continuous work,
but live-labeled continuous mode is still hard-blocked. Removing that block
would not yet be a valid food-procurement result: the current plugin does not
export authoritative game time, exact dialogue target/option text, or the
currently visible inventory tooltip; purchase validation accepts any verified
non-hostile owner rather than the plan's exact target; and no deterministic
policy confines a live continuous plan to the calibrated Barman chain.

Scope:

- Add additive, capability-gated native observations for in-game elapsed time,
  exact dialogue target, bounded dialogue options, and bounded visible tooltip
  text. Missing or unreadable UI evidence remains null/unavailable.
- Extend the typed condition language only with the scalar paths and string
  containment needed to express those observations.
- Add a versioned `food_procurement_v1` live-continuous policy. The default
  remains disabled; the policy is native-assisted-only and accepts only the
  calibrated world/dialogue/trade phases and action order.
- Bind every phase to one stable vendor ID. Require exact first dialogue text
  and exact trade ownership. Before purchase, require the visible tooltip to
  contain the model-named item, `[Food]`, and exact expected price, and require
  the click to lie inside the current tooltip source widget. This direct native
  binding is stronger than trusting planner history about a prior coordinate.
- Require exact later money and food-count deltas plus confirmed pause after
  purchase. Preserve at-most-once purchase treatment and all existing plan,
  guard, causal-revision, supervisor, and cleanup boundaries.
- Add a separate explicit CLI acknowledgement before live continuous actions.
- Prove the full multi-action conditional chain and all mismatch aborts in a
  deterministic live-shaped fake environment before any Kenshi run.

Non-goals:

- No generic trader, inventory grid, tooltip parser, resolution, or
  interface-only claim.
- No arbitrary live-continuous plan execution outside
  `food_procurement_v1`.
- No new native action or automatic retry of an accepted/ambiguous vendor
  command.
- No supervised live test until the offline implementation, native build, and
  documentation gates pass and the user is notified immediately beforehand.

Acceptance criteria:

- One strategic response executes approach, exact dialogue choice, and item
  inspection without another strategic call; a later tooltip-grounded response
  may execute one purchase.
- The executor rechecks fresh pause, one exact selection, stable target role,
  exact dialogue target/option, exact trade owner, and current tooltip evidence
  immediately before the relevant action.
- Changed selection, target, role, screen, dialogue option, ownership, tooltip,
  price, item name, inspection coordinate, balance, or capability prevents the
  sensitive future action before dispatch.
- Purchase success requires causally later exact money and food-count changes
  and `paused=true`; inconclusive delivery is never retried automatically.
- Emergency stop, human input, stale/stalled telemetry, and capability loss can
  still preempt independently of a blocked planner or running approach.
- Default/live-example configuration remains unable to execute live continuous
  work. The calibrated profile requires the policy flag, existing live/native
  gates, and the new live-continuous acknowledgement.
- Strict schemas, replay/evaluator output, fixed single-step seeds, the portable
  continuous proof, and all prior tests remain green. The pinned Release x64
  native project builds before live validation is considered.

Implementation status: offline-complete, installed, read-only-live-validated,
conditional-action-live-pending. The deterministic live-shaped proof performs
four actions from two strategic calls and confirms the exact 649-cat debit,
one-food increase, and pause. All 178 Python tests, lint, source types, compile
checks, schema export, and the pinned VS2010 SP1 Release x64 build pass. The
initial protocol `0.4.0` DLL was 182,784 bytes with SHA-256
`64a3cf3c22fc4ee04152c6a70a143f16cb59e82ebb8d62e5a2cc885acfb77cfe`.
The full prior protocol `0.3.0` plug-in is backed up at
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T193734Z-p6-protocol-0.4`.
After the first long live session froze its atomic snapshot at sequence 3985
while Kenshi remained foreground, paused, rendering, and responsive, the
sampler gained exception-safe latch release and four bounded retries for
transient Windows replace/share failures. The current installed hotfix is
183,296 bytes with SHA-256
`0096082215cbc1f842a8947291570328481c78cab9c23b8ae00a4dcdf6e888a3`.
The full replaced protocol `0.4.0` plug-in is backed up at
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T202819Z-p6-stream-hotfix`.
Kenshi relaunched successfully with the hotfix. Fresh protocol `0.4.0`
telemetry, stable identities, game time, dialogue/tooltip capabilities, strict
validation, pause, and zero-command baseline were confirmed live; post-relaunch
samples advanced 61 -> 69 -> 78. No conditional action has yet been dispatched.

Hosted live preflight on 2026-07-23:

- Read-only launch validation passed at telemetry sequence 168. Later
  observation at sequence 3186 remained fresh, loaded, paused, responsive, at
  1,000 cats and zero food, with native command sequence zero.
- Dry run `p6-live-continuous-dry-20260723T1949Z` exposed an unbounded
  planner-validation error crossing the runtime's own bounded rationale field.
  The runtime now truncates the safe-stop rationale and preserves a separate
  bounded diagnostic event; a 20,000-character regression case passes.
- Dry run `p6-live-continuous-dry-20260723T1955Z` then stopped safely on the
  90-second `xhigh` hosted-call timeout with no action or native command.
- Dry run `p6-live-continuous-dry-high-20260723T1956Z` returned in 62.7
  seconds and stopped safely because the model invented shorthand condition
  paths and malformed freshness conditions. This was a structured-schema gap,
  not an action-policy bypass.
- Dry run `p6-live-continuous-dry-medium-20260723T201345Z` returned in 31.45
  seconds and stopped safely on the remaining `exists` cross-field shape.
  Money, food, pause, and native command sequence remained unchanged.
- Dry run `p6-live-continuous-dry-medium-20260723T201759Z` was independently
  preempted after 18.91 seconds when telemetry stopped at sequence 3985 for
  three consecutive supervisor checks. Kenshi remained paused, responsive,
  foreground, and visually intact with no native command. The sampler hotfix
  and clean relaunch followed before another planner call.
- Dry run `p6-live-continuous-dry-medium-20260723T203207Z` returned a strict
  `PlanEnvelope` in 23.95 seconds and kept the repaired stream advancing. The
  deterministic policy rejected it with zero actions because redundant
  non-target IDs obscured otherwise-present checks, its game-time budget was
  too small, and the exact planner revision had naturally advanced. Global
  conditions now canonicalize redundant IDs, world-phase game time must cover
  the approach duration plus one second, and only this live policy can rebase
  sequence-only latency across an unchanged exact phase fence. Generic stale
  output remains rejected.
- Dry run `p6-live-continuous-dry-medium-20260723T204004Z` returned a
  structurally correct world-phase plan in 25.20 seconds. Rebase correctly
  stopped because `ui` differed; inspection showed only transient
  `client_width/client_height` changing from 1920x1080 to null while all
  policy-authoritative UI state remained identical. Capture dimensions are now
  excluded from the gameplay fence. Trusted policy code also compiles
  canonical conditions, graph, timeouts, and risks after action
  structure/target/arguments match, rather than trusting duplicated hosted
  boilerplate. The run sent zero actions.
- Hosted live planning now defaults to `medium` reasoning. Direct OpenAI calls
  receive a deterministic output-token ceiling of 4,096 for one decision,
  growing by 2,048 per bounded plan step to at most 12,288. The runtime
  mechanically requests `PlannerDecision`, `PlanEnvelope`, or future-only
  `PlanPatch` from current planning state.
- Every supported condition path is now a strict schema enum, so the hosted
  response cannot invent shorthand paths. The unused `exists` operator was
  removed because its conditional `expected` shape was not represented by the
  strict schema. `food_procurement_v1` success checks also preserve the exact
  one-character selection invariant after every action.
- Repository verification after these changes: 182 tests passed; Ruff passed;
  mypy passed for all 47 source files; compileall, schema export, diff check,
  and default doctor passed. The next gate is another zero-action hosted dry
  response before enabling any live input.

## Completed milestones

- Deterministic mock environment and one-day survival baseline.
- Strict telemetry, observation, action, decision, receipt, and memory models.
- Guarded Windows input, bounded movement pulses, and outcome feedback.
- Narrow supervised Hub Barman approach/trade/one-item purchase evidence.
- Typed, mechanically enforced control modes carried through observations,
  receipts, run lifecycle events, overlays, CLI/log summaries, schemas,
  benchmarks, and current documentation.
- Typed bounded plan graphs, five-valued capability-aware conditions,
  executor-owned causal verification and budgets, lifecycle replay, and a
  deterministic two-action continuous mock proof.
- One cancellable observation pump, bounded authoritative world-state stream,
  causal command receipts, transient-event journal, isolated subscribers, and
  ambiguity-aware portable entity lifetimes.
- Independent blocked-work preemption, conservative cancellation causality,
  narrowly guarded safe-pause cleanup, and supervisor-specific lifecycle
  metrics.
- Stateful configured-movement lifecycle, concurrent future advisory,
  optimistic patch staging/application, stale-output rejection, and option
  replay metrics.
- Stable native squad/selection/nearby/target identity, explicit lifecycle
  semantics, legacy-source fallback, and a supervised live identity proof.
- Causal native command requests with exact issue-time fences, bounded keyed
  lifecycle acknowledgements, replay metrics, and a supervised stale-rejection
  plus exact-target completion proof.

## Previous completed slice: explicit control modes

Problem: the repository claims UI-only/read-only control while the native plugin
also exposes a hotkey that issues `PLAYER_TALK_TO` through a Kenshi internal
player-order method. That makes safety claims and future continuous-plan
evidence ambiguous.

Scope:

- Add typed `interface_only` and `native_assisted` modes, defaulting to
  `interface_only`.
- Mark native-assisted skills in configuration and schemas.
- Omit and reject those skills in `interface_only`, with both policy and
  environment enforcement.
- Require a dedicated configuration opt-in and CLI acknowledgement before
  native-assisted live execution.
- Carry the mode through observations, receipts, run events, overlays,
  summaries, metrics, and current documentation.

Non-goals:

- No new action capability.
- No continuous executor or telemetry transport changes.
- No Windows or live Kenshi execution.

Acceptance criteria:

- Automated tests prove interface-only omission/rejection and native-assisted
  opt-in/acknowledgement.
- Every new run artifact states its control mode.
- README, status, architecture, safety guidance, schemas, and live profiles
  agree with the implementation.
- The full portable baseline, static checks, doctor, schema comparison, and
  deterministic mock seeds pass.

Result: complete in the current worktree. No new action surface was added.
Interface-only filtering and rejection are enforced independently by the live
observation/environment boundary and `ActionGuard`.

## Previous completed slice: P1 typed bounded plans

Problem: `PlannerDecision` carried exactly one action and `AgentRuntime` waited
for a complete planner/action/observation cycle before every next action.
Continuity was therefore structurally impossible even when the prompt described
a longer intention.

Scope:

- Preserve `single_step` unchanged as the default and add feature-flagged
  `continuous` planning.
- Add strict, schema-exported world revisions, capability-aware typed
  conditions, bounded acyclic plan graphs, explicit control-mode binding,
  retries/idempotency, branches, action/wall/game/risk budgets, plan versions,
  and patch concurrency data.
- Add deterministic condition and policy evaluators that distinguish `false`,
  `unknown`, `unavailable`, and `stale`.
- Execute multiple plan steps through the existing guard/environment path
  without another strategic planner call.
- Re-evaluate plan assumptions and step preconditions immediately before every
  action; cancel a stale future step before guard validation or execution.
- Evaluate success/failure only on a causally later world revision.
- Emit replayable plan lifecycle events and add evaluator metrics for strategic
  calls, plan/step outcomes, actions per call, and stale/rejected work.
- Prove the first vertical slice in mock/fake event-driven environments only.

Non-goals:

- No independent observation-pump task or world-state event store (P2).
- No planner-concurrent safety supervisor (P3).
- No stateful live movement option or overlapping strategic planning (P4).
- No native command ID/stable-identity change and no live food chain.

Acceptance criteria:

- One planner response executes at least two guarded actions and logs one
  strategic call.
- A changed assumption or precondition cancels the next action before the
  environment sees it.
- A desired state on an old-but-wall-clock-fresh revision cannot satisfy a
  postcondition.
- Missing capabilities and null values remain `unavailable`/`unknown`, never
  false.
- Invalid branch targets, cycles, unreachable steps, unsafe retries, excessive
  horizon, and policy-exceeding budgets are rejected.
- Plan lifecycle replay reaches the same terminal state and metrics report
  calls, actions, completed steps, rejections, aborts, and causal failures.
- Existing single-step tests and fixed-seed outcomes remain green.
- Schemas, configs, planner adapters, prompt, docs, and ledger agree.

Result: complete in the current worktree. The default path remains
`single_step`. Continuous mode now accepts a versioned, bounded plan, executes
multiple actions through the ordinary guard/environment path, and refuses stale
plans or future steps before execution. It distinguishes condition uncertainty,
requires later revisions for postconditions, emits replayable lifecycle and
transactional budget events, and is deliberately blocked in live-labeled
environments pending later milestones.

## Previous completed slice: P2 world-state stream and causal waits

Problem: P1 still asks the executor to poll `environment.observe()` directly
while waiting for a postcondition. There is no single authoritative observation
stream, bounded event history, subscriber API, or active-command registry, so
future safety supervision and planner/executor overlap would either race or
poll the transport independently.

Scope:

- Add one independently running observation pump with explicit start/stop
  ownership and optional visual-capture requests.
- Add a bounded world-state store that validates monotonic telemetry/frame/
  capability revisions, detects duplicates and regressions, preserves the last
  visual frame across telemetry-only observations, and exposes immutable latest
  snapshots.
- Retain bounded state deltas and transient observation events even after they
  disappear from the latest snapshot.
- Add a stable nearby-entity registry with explicit observed lifetimes that
  survives source-ID reordering and closes a lifetime when the entity vanishes.
- Add bounded subscriber queues and causal `wait_for` APIs that cannot succeed
  from the starting revision.
- Track active plan/step and command ID/start/completion revisions in
  deterministic runtime state.
- Integrate continuous plan acceptance, pre-action reads, and postcondition
  waits with the store. Keep `single_step` behavior unchanged.

Non-goals:

- No independent safety supervisor or blocked-planner preemption (P3).
- No live movement option, cleanup protocol, planner/executor overlap, or active
  future-step patch application (P4).
- No stable Kenshi-native character ID claim; the portable registry is an
  evidence-based continuity layer and exposes ambiguity.
- No live continuous execution.

Acceptance criteria:

- Duplicate/stalled sequences are observable and regressing revisions are
  rejected.
- A causal wait starting at revision `R` never succeeds from `R` or earlier.
- Transient events remain queryable after the latest observation no longer
  contains them.
- Entity identity survives source-ID reordering, while disappearance closes the
  old lifetime.
- Multiple subscribers receive the same update without polling the environment.
- Command completion rejects a mismatched command ID and records causal start/
  completion revisions.
- Pump stop/cancellation leaves no task or subscription leak; fake-clock tests
  remain deterministic.
- Continuous runtime postconditions use the store, and a planner result that
  became stale while awaiting the planner is rejected before any action.

Result: complete in commits `c48bc2b` and the following documentation commit.
Continuous mode now has a single validated stream rather than executor-owned
transport polling. Raw changes on an unchanged revision cannot become progress,
transient events and entity lifetimes survive snapshot replacement, and bounded
subscribers share one ingest path. The default single-step behavior and live
continuous block remain intact.

## Latest completed slice: P3 independent safety supervisor

Problem: P2 keeps observation moving while the strategic planner is blocked,
but immediate safety still depends on the scheduler reaching its next reflex
check. A slow or hung planner can therefore delay deterministic pause/stop
behavior even when the state stream has already exposed a threat, stale stream,
capability loss, or unexpected unpause.

Scope:

- Add one independent supervisor task subscribed to the P2 store.
- Detect deterministic reflexes, stale/stalled telemetry, pause-capability
  withdrawal, and unpaused state with no active authorized command.
- Race strategic planner and active-plan execution tasks against supervisor
  preemption without waiting for model completion.
- Cancel obsolete planner/executor work once, record uncertain dispatched
  commands conservatively, and emit supervisor-specific lifecycle evidence.
- Route safe-pause cleanup through the existing guard/environment path, bind it
  to a command ID and causal revision, and require a later confirmed paused
  state before reporting safe cleanup.
- Make repeated preemption and stop calls idempotent and leak-free.

Non-goals:

- No live continuous enablement, controller worker-thread polling, or claim of
  measured F12/human-input latency.
- No general stateful option abstraction or planner/executor overlap (P4).
- No active plan-patch application or strategic recovery policy.
- No weakening of the existing live movement pulse's own guaranteed re-pause.

Acceptance criteria:

- A fake planner deliberately blocked on an await is cancelled when the pump
  publishes an unsafe state.
- A blocked fake movement action is cancelled and followed by one guarded pause
  request with a causally later confirmed paused revision.
- Repeated preemption produces one cleanup and one terminal supervisor event.
- Planner/executor cancellation leaves no active command, plan, subscription,
  or owned task.
- Supervisor actions and causes are distinguishable from planner/reflex
  decisions in logs and evaluator metrics.
- Existing single-step, P1, and P2 behavior remains green; continuous live
  execution remains blocked.

Result: complete in `8f1c9c2`. The first deterministic preemption is latched
from immutable store updates, obsolete planner/executor tasks are canceled once,
and uncertain in-flight dispatch remains spent and inconclusive. The only
cleanup exception is `PauseAction(paused=true)`; it preserves allowlist and
control-mode checks, bypasses only the rate counter, and requires a later
capable paused revision. Failure and missing capability remain explicit. The
portable live-continuous block is unchanged.

## Latest completed slice: P4 stateful movement-option lifecycle

Problem: movement is still represented to the continuous executor as one opaque
`environment.step()` await. P3 can cancel that await, but there is no typed
prepare/start/poll/cancel lifecycle, no option-specific progress record, and no
safe seam for a concurrent strategic advisory or future-only patch.

Scope:

- Adapt configured movement-pulse skills into an executor-owned stateful option
  without deleting the existing macro implementation.
- Give the option explicit prepared/running/succeeded/failed/cancelled states,
  immutable start evidence, state-stream polling, and idempotent cancellation.
- Log and evaluate option lifecycle separately from plan-step and raw action
  events.
- Run one strategic advisory concurrently with an active portable movement
  option. Stage only a matching, current, bounded `PlanPatch`; discard errors,
  wrong output types, stale bases, and version/plan mismatches.
- Revalidate a staged future-only patch against the post-option revision,
  remaining action/risk/time budgets, assumptions, and ordinary per-step guards
  before applying it. Never alter or restart the active/completed step.
- Keep `single_step`, non-movement continuous actions, and live-continuous
  blocking behavior unchanged.

Non-goals:

- No live continuous enablement or rewrite of the proven live movement pulse.
- No native command bridge/stable-handle change.
- No broad option conversion beyond configured movement-pulse skills.
- No claim of Windows user-input/F12 latency from portable tests.

Acceptance criteria:

- A fake movement option exposes prepared, started, progress, and succeeded
  events while one strategic patch call overlaps it.
- A matching patch returned before the movement completes is not applied until
  the movement succeeds and the patch passes a second latest-state/budget
  validation; only its future steps execute.
- A stale or mismatched concurrent output executes no future action and is
  logged as discarded/rejected rather than a planner crash.
- Safety cancellation during the option produces one option-cancelled event,
  one inconclusive command outcome, and the existing single confirmed-pause
  cleanup.
- Completion, failure, and repeated cancellation leave no option task,
  advisory task, subscription, active command, or active plan.
- Full portable gates, the continuous two-action proof, and fixed single-step
  seeds remain green; live/Windows behavior remains untested.

Result: complete in `58736e8`. Configured movement pulses now have explicit
prepared/running/progress/succeeded/failed/cancelled state while retaining the
existing macro/environment mechanics. One immutable active-plan observation may
produce a matching future-only patch during movement. The running/completed
step is protected; a staged patch is applied only after movement succeeds and
the latest revision, assumptions, topology, policy, and remaining budgets pass
again. Stale/mismatched output stays advisory. Human-input events and safety
preemption cancel the option through P3's single verified-pause path. Live
continuous mode remains blocked.

## Latest completed slice: P5 stable native identity

Problem: native squad and nearby IDs were list ordinals, and the bridge retained
only a target display name. List reorder, duplicate names, object reuse, and
save/process transitions could therefore alias identity.

Scope:

- Bump native telemetry to additive protocol `0.2.0`.
- Derive opaque IDs from validated Kenshi handle components plus process and
  game-session generations; never serialize pointers.
- Use those IDs for squad, full selection set, nearby entities, and the legacy
  native target diagnostic.
- Define birth/update/tombstone and session-change semantics.
- Strictly validate session metadata, unique IDs, selection membership, and
  squad selection agreement when the capability is asserted.
- Preserve native IDs exactly in the Python store while retaining the
  ambiguity-aware registry for legacy ordinal producers.

Non-goals:

- No caller-driven native command transport.
- No claim that the current native acknowledgement is causal or complete.
- No live continuous enablement or food-chain generalization.

Acceptance evidence:

- Automated tests preserve two duplicate-named IDs across reorder, tombstone an
  omitted ID, and prevent the same handle-shaped value in a new session from
  aliasing the old ID.
- The pinned VS2010 SP1 Release x64 build passed.
- Live protocol `0.2.0` agreed on exactly one primary/set/squad selection.
  Eighteen nearby characters had eighteen IDs, including four distinct
  same-named Ninja Guards.
- A paused camera orbit changed presentation but not session, selection, or the
  complete nearby ID set. Native query order did not change in that run, so
  live reorder is not claimed.
- A later renderer reset in the same process was diagnosed separately. The
  prior DLL reproduced the same DirectX device-removal/driver-internal error
  after a ten-minute baseline, while the identity DLL passed a subsequent
  mitigated ten-minute soak and clean exit. This rules out stable identity as a
  necessary cause; it does not establish broad live stability.

Result: complete in `28489cc`. The first live pass was usefully
rejected because Kenshi's overloaded handle equality disagreed with the
selected-set handle fields. Direct equality over type, container, container
serial, index, and serial fixed the mismatch; rebuild and reload passed. The
legacy hotkey acknowledgement remains explicitly non-causal. The separate GPU
incident and reversible mitigation are recorded in
`docs/LIVE_STABILITY_INCIDENT_20260723.md`.

## Latest completed slice: P5 causal native command envelope

Implementation status: portable code/tests, schemas, documentation, replay
metrics, the pinned DLL build, and supervised live rejection/acceptance/
completion/final-pause validation are complete.

Problem addressed by this slice: `approach_confirmed_vendor` sent only a private
hotkey. The plugin chose a nearest role match and exposed one mutable result, so a
pre-command snapshot, old acknowledgement, changed selection, replaced target,
or later run can be mistaken for the current caller's command. That breaks the
same causal ownership rules already enforced by the portable continuous
executor.

Scope:

- Carry the executor/runtime-owned unique command ID and complete based-on
  world revision through the environment dispatch boundary.
- Require the native-assisted vendor action to name one exact stable target ID;
  bind the request to `native_assisted`, the current identity session, and the
  exact one-member selection set observed during guard validation.
- Atomically write a strict bounded request before sending the private hotkey.
- Make the native game/UI-thread bridge parse that request, reject malformed,
  duplicate, stale, wrong-mode, wrong-session, selection-mismatched, missing,
  replaced, or role-invalid targets without issuing a player order.
- Emit bounded acknowledgements keyed by command ID with accepted/rejected/
  completed/cancelled status, reason, target, selection, request basis, and
  causal telemetry sequences.
- Keep accepted target/selection handles under observation; cancel on lifetime
  or selection change and complete only when the exact target becomes the
  active dialogue conversation target.
- Make Python wait only for the matching command's acknowledgement on a later
  telemetry revision. A different or old acknowledgement is not progress.

Non-goals:

- No live-continuous enablement.
- No generic native method dispatcher or additional native action.
- No P6 dialogue, trade, or purchase chain.
- No automatic retry after ambiguous dispatch or acknowledgement timeout.

Acceptance criteria:

- Strict model tests reject a missing telemetry basis, non-native mode,
  non-exact selection, malformed ID, and inconsistent acknowledgement
  sequences.
- The ordinary continuous executor passes its own command ID and based-on
  revision through both atomic and stateful-option dispatch; single-step live
  dispatch also supplies a unique caller ID.
- Live-environment tests prove the request is written before the hotkey, an old
  acknowledgement cannot satisfy a new command, exact rejection performs no
  movement pulse, and accepted acknowledgement is tied to a later telemetry
  revision.
- Native contract/build evidence covers exact target lookup, strict request
  fences, duplicate rejection, selection/target-loss cancellation, and exact
  dialogue-target completion.
- Existing single-step and portable continuous behavior, causal receipts,
  safety cancellation, option cleanup, schemas, fixed seeds, and control-mode
  separation remain green.
- Supervised live evidence records exact commit/config/plugin hashes, request
  and acknowledgement IDs/revisions, target/selection IDs, final paused state,
  and any operator intervention. If live validation is unsafe or unavailable,
  that gate remains explicitly open rather than being inferred from a build.

## Current checks

P0 semantic-launch/control-ownership offline verification on 2026-07-23:

- `.venv/bin/python -m pytest -q`: 209 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src`: passed, 48 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `interface_only` / `single_step`.
- A fresh temporary schema export matched `schemas/` byte-for-byte.
- Mock seeds 7, 11, and 19 survived one in-game day in 25, 13, and 13 actions.
- Continuous run `p0-semantic-launch-continuous-proof` completed both guarded
  plan steps with later command revisions and no rejected action. Its third
  receipt is the heuristic's explicit terminal Stop.
- Focused tests prove resettable countdown timing, terminal F12 disarm, visible
  overlay states, fresh post-handoff replanning, semantic-label normalization,
  duplicate-label rejection, current-bound clicks at arbitrary normalized
  positions, and an in-lease anchor-change zero-input result.
- Protocol `0.5.0` built with the pinned VS2010 SP1 Release x64 toolchain. The
  185,344-byte DLL SHA-256 is
  `a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`.
  It was installed for the first supervised load, and `OpenSettingOnStart`
  remained false.
- The 1920x1080 smoke reached a responsive title screen without the optional
  RE_Kenshi panel. Fresh plug-in status reported `ready`, but title telemetry
  did not replace the prior loaded-session snapshot because sampling was still
  tied to `PlayerInterface::update`. The semantic launcher timed out with zero
  pointer input and Kenshi closed normally from the title screen.
- A lifecycle hotfix moves two-hertz sampling to MyGUI's title-and-game
  `Gui::frameEvent` path and keeps native-command monitoring on
  `PlayerInterface::update`. Focused contract/launcher tests pass, and the
  pinned Release x64 build is 186,368 bytes with SHA-256
  `ace964357eaa93c8844d1b564447bf85650dba97434f67f7875cdb03f1de88d5`.
  Installation and repeat live smoke are the immediate gate.
- Alternate-resolution semantic startup and visible ownership reset/disarm
  remain live gates.

P0 launcher/calibration recovery verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 194 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src`: passed, 47 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `interface_only` / `single_step`.
- A fresh temporary schema export matched `schemas/` byte-for-byte.
- Focused launcher/config/live-environment evidence: 28 passed. It covers
  human input before the lease, human input after lease acquisition, exact
  1920x1080 acceptance, 1280x720 rejection, and a client-size change inside
  the acquired live input lease with zero primitive actions.
- Mock seeds 7, 11, and 19 survived one in-game day in 25, 13, and 13 actions.
- Continuous run `p0-launcher-continuous-proof` completed two guarded actions
  from one strategic call.
- No fresh supervised Windows launch has exercised the revised launcher.
  Kenshi remains stopped at the pre-live-test boundary.

P5 causal-command offline verification on 2026-07-23:

- `.venv/bin/pytest`: 160 passed.
- `.venv/bin/ruff check src tests`: passed.
- `.venv/bin/mypy src`: passed, 46 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `interface_only` / `single_step`.
- Fresh exported schemas matched checked-in `schemas/` byte-for-byte, including
  the new native-request schema and acknowledgement-bearing telemetry/receipt
  schemas.
- The pinned VS2010 SP1 Release x64 build passed. Its 175,104-byte offline DLL
  SHA-256 is
  `9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`.
  It emitted upstream MyGUI C4091 and Boost 1.60 property-tree C4715 warnings.
- Mock seeds 7, 11, and 19 survived one day in 25, 13, and 13 actions.
- Continuous run `p5-causal-continuous` completed two guarded actions from one
  strategic call. Both receipts had later causal revisions; replay metrics
  reported 100% post-command revisions, no rejected actions, no command/
  revision/stream errors, and one retained transient event.
- No DLL was staged, installed, launched, or exercised by the offline checks;
  the separate supervised evidence below closes the live gate.
- The installed DLL SHA-256 was
  `9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`;
  the prior DLL was backed up under
  `%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T184326Z-p5-causal`.
- Stale command `cmd-fc8d78b68bf54babb8d6f360a14f4bbc` was rejected as
  `stale_revision` at sequence 170 with unchanged selected position, no active
  command, and confirmed pause.
- Current command `cmd-77f7735532484c11b0be9cb46fb29081` was based on
  sequence 248, accepted at 249 for the exact selected Hep and Barman target
  IDs, and completed at 423 only when exact-target dialogue opened. It cleared
  active state and remained paused. Two explicitly recorded operator
  continuation pulses advanced the already issued task without another native
  command.
- Final telemetry 475 retained both keyed acknowledgements and pause. Kenshi
  closed normally; no new plugin error, DXGI device removal, BAD STUFF message,
  or Windows Application error was found.

P5 stable-identity boundary verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 151 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 45 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/python -m kenshi_agent doctor --config config/default.yaml`:
  passed and reported `control_mode interface_only` and `planning_mode
  single_step`.
- Fresh schema export matched checked-in `schemas/` byte-for-byte.
- The pinned VS2010 SP1 Release x64 native build passed with only the existing
  upstream MyGUI C4091 warning. The rebuilt plugin loaded through RE_Kenshi,
  reached fresh ready/telemetry state, and left the save paused.
- Live identity evidence covered exact one-selection agreement, 18 unique IDs
  for 18 nearby characters, four IDs for four same-named Ninja Guards, and
  unchanged session/selection/nearby ID set across a paused camera change.
- Crash triage matched Windows SDK constants for a DirectX device reset and
  internal driver error. The prior DLL reproduced it; Low textures plus
  disabled water reflections then passed a more-than-ten-minute identity soak
  and clean exit under continued system-memory pressure.
- Single-step seeds 7, 11, and 19 retained the one-day outcomes in 25, 13, and
  13 actions.

P4 completion verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 146 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 45 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `control_mode interface_only` and `planning_mode single_step`.
- Fresh schema export matched checked-in `schemas/` byte-for-byte; the
  observation schema now includes nullable `ActivePlanContext`.
- Deterministic fake movement proved concurrent patch staging before option
  success, post-option revalidation/application as plan version 2, execution of
  only the replacement future step, and matching lifecycle replay/metrics.
- A concurrent advisory made stale by a pump update was rejected and the
  original future step ran. A `human_input_detected` event cancelled blocked
  movement, left its command inconclusive, and produced one confirmed pause.
- Continuous run `p4-final-verified` retained the ordinary two-action/one-call
  proof with two later-revision receipts and no option/supervisor activity.
- Single-step seeds 7, 11, and 19 retained the one-day outcomes in 25, 13, and
  13 actions.

P3 completion verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 138 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 44 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `control_mode interface_only` and `planning_mode single_step`.
- A fresh schema export matched checked-in `schemas/` byte-for-byte.
- Continuous run `p3-final-verified` completed two guarded actions from one
  strategic call with two causal receipts, 100% later-revision coverage, no
  stream errors, and no supervisor preemption in the ordinary safe case.
- Deterministic blocked-planner and blocked-movement tests each recorded one
  supervisor preemption and one confirmed safe-pause terminal. The explicit
  unconfirmed-pause case recorded one cleanup failure and no false completion.
- Single-step seeds 7, 11, and 19 retained the one-day outcomes in 25, 13, and
  13 actions.

P2 completion verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 129 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 43 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `control_mode interface_only` and `planning_mode single_step`.
- A fresh schema export matched checked-in `schemas/` byte-for-byte, including
  the new standalone `receipt.schema.json`.
- Continuous run `p2-final-verified` completed two guarded actions from one
  strategic call. Its summary reported two causal command receipts, 100% with
  post-command revisions, one retained transient event, no stream errors,
  `actions_per_strategic_planner_call: 2.0`, and `control_mode:
  interface_only`.
- Single-step seeds 7, 11, and 19 retained the one-day outcomes in 25, 13, and
  13 actions.

P1 completion verification on 2026-07-23:

- `.venv/bin/python -m pytest -o addopts='' -q`: 111 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src/kenshi_agent`: passed, 42 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
  reported `control_mode interface_only` and `planning_mode single_step`.
- Fresh exported schemas matched checked-in `schemas/` byte-for-byte, including
  `plan.schema.json` and `plan_patch.schema.json`.
- Continuous run `p1-continuous-proof` completed `resume` and `accelerate` from
  one strategic call. Summary metrics reported two executed actions, two
  committed reservations, one completed plan, and
  `actions_per_strategic_planner_call: 2.0`; lifecycle replay reconstructed the
  same completed plan and step order.
- Single-step seeds 7, 11, and 19 retained the prior one-day outcomes in 25, 13,
  and 13 actions.

Baseline at `ebfe9248f2adabe1cb6ebf264ecb9ad67fec3c68` on 2026-07-23:

- `.venv/bin/python -m pytest -q`: 85 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/mypy src`: passed, 40 source files.
- `.venv/bin/python -m compileall -q src scripts`: passed.
- `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed.
- Exported schemas matched `schemas/` byte-for-byte.
- Mock seeds 7, 11, and 19 each survived one in-game day.
- `.venv/bin/python -m pip install -e ".[dev]"` was unavailable because this
  virtual environment has no `pip` module; the equivalent
  `uv pip install --python .venv/bin/python -e ".[dev]"` succeeded.

## Evidence

- Automated portable evidence for P4 covers option prepare/start/progress/
  success/failure/cancel states, idempotent cancellation, cancellation cleanup
  failure, concurrent advisory overlap, immutable active-plan context, valid
  future-only patch staging and post-option application, protected step IDs,
  stale patch rejection, versioned replay, option/patch evaluator metrics,
  human-input preemption, and owned task/subscription cleanup.
- Automated portable evidence for P3 covers blocked-planner cancellation,
  cancellation during fake movement dispatch, conservative command/budget
  treatment, later-revision pause confirmation, cleanup timeout/failure,
  missing pause capability, capability withdrawal, consecutive stalled
  sequences, immutable authorization snapshots, rate-budget-safe pause,
  idempotent preemption/stop, subscription cleanup, distinct lifecycle events,
  and evaluator metrics.
- Automated portable evidence for P2 covers duplicate/regressing/conflicting
  revisions, capability withdrawal, causal wait timeout/cancellation, bounded
  histories and subscriber overflow, isolated subscriber data, transient-event
  retention/loss, visual carry-forward, entity ordinal reorder/destruction,
  same-name identity ambiguity, command mismatch/completion, pump capture and
  shutdown, stale planner output, causal receipts, and unchanged-revision
  inconclusive outcomes.
- Automated portable evidence for the previous control-mode slice:
  - `.venv/bin/python -m pytest -q`: 91 passed.
  - `.venv/bin/ruff check .`: passed.
  - `.venv/bin/mypy src`: passed, 40 source files.
  - `.venv/bin/python -m compileall -q src scripts`: passed.
  - `.venv/bin/kenshi-agent doctor --config config/default.yaml`: passed and
    reported `control_mode interface_only`.
  - Fresh exported schemas matched checked-in `schemas/` byte-for-byte.
  - Mock seeds 7, 11, and 19 survived one day in 25, 13, and 13 actions.
  - Run `20260723T145942.137422Z` and its `kenshi-agent summarize` output both
    reported `interface_only`; all 25 receipts carried the same mode.
- Existing Windows and supervised live evidence predates explicit control-mode
  labeling and must not be merged into either mode's future metrics.
- Windows PowerShell launchers, Windows input, native build/load, and live
  Kenshi behavior were not tested in this slice.

## Known risks and deferred debt

- Broad live stability remains open. The Intel Iris Xe client reproduced the
  DirectX device reset after roughly forty minutes even with Low textures and
  reflections disabled. View distance is now 2500, but that profile has not
  completed a fresh supervised soak.
- The plugin transport remains an atomically replaced latest snapshot. One
  Python pump now ingests it into an event stream, but this is not native event
  transport.
- Strategic overlap and active patch application are intentionally limited to
  the portable configured-movement option adapter.
- General continuous live execution and stateful live movement options remain
  blocked; only `food_procurement_v1` is eligible behind all dedicated gates.
- Legacy telemetry producers may still expose ordinal IDs; only snapshots with
  `identity.stable_handles` receive native identity trust.
- Observation payload truncation can produce malformed JSON.
- Several declared config fields remain behaviorally unused.
- There is no CI workflow or Python lockfile.

## Ordered next candidates

1. P0: supervised 1920x1080 launcher interruption and reduced-view-distance
   stability smoke, after a fresh user handoff.
2. P6: resume the exact conditional live food-procurement chain only after the
   P0 launch/stability gate closes and explicit live authorization remains
   current.
3. P8: semantic observation budgeting that always emits valid JSON, followed
   by CI and a reproducible Python lockfile as separate bounded slices.
