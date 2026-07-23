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
  strict bounded `PlanEnvelope` in mock/fake environments only; live-labeled
  environments terminate before a strategic call or action.
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

## Current checks

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

- Native bridge acknowledgement still lacks command IDs and revision fences;
  current command causality is owned inside the portable Python runtime.
- The plugin transport remains an atomically replaced latest snapshot. One
  Python pump now ingests it into an event stream, but this is not native event
  transport.
- Strategic overlap and active patch application are intentionally limited to
  the portable configured-movement option adapter.
- Continuous live execution and stateful live movement options are
  intentionally blocked; the existing live pulse remains monolithic.
- Raw nearby and squad ordinal IDs remain unstable. The portable nearby
  registry normalizes observed lifetimes but does not prove native identity.
- Observation payload truncation can produce malformed JSON.
- Several declared config fields remain behaviorally unused.
- There is no CI workflow or Python lockfile.

## Ordered next candidates

1. P5: native bridge command acknowledgements and validated stable-handle
   generations without conflating them with the portable lifetime registry.
2. P6: the first conditional live food-procurement chain after P5 evidence
   and explicit live authorization.
3. P8: semantic observation budgeting that always emits valid JSON, plus CI
   and a reproducible Python lockfile.
