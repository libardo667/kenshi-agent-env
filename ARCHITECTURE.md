# Architecture

The system separates observation, deliberation, action, memory, and evaluation
so failures can be attributed instead of blurred together.

```text
Kenshi process
  └─ KenshiLib plugin (game/UI thread)
       ├─ observational telemetry ──> atomic telemetry.latest.json
       └─ reviewed native command bridge (native_assisted only)

Python runtime
  ├─ telemetry reader ─────────┐
  ├─ triggered screenshot ─────┼─> observation pump
  ├─ SQLite memory ────────────┘          │
  │                                      v
  │                            bounded world-state store
  │                              ├─ latest + deltas/events
  │                              ├─ entity lifetimes
  │                              ├─ active plan/command
  │                              └─ subscriber queues
  │                                      ├─> safety supervisor
  │                                      │     └─ cancel + guarded safe pause
  │                                      └─> scheduler/executor
  │                                             └─ stateful movement option
  │                                                   ↕ future-only advisory
  ├─ reflex layer (shared deterministic pause/stop rules)
  ├─ planner (heuristic, scripted, subprocess, or vision LLM)
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ executor
       ├─ interface_only ──> Windows SendInput ──> ordinary Kenshi UI
       └─ native_assisted ──> marked bounded bridge skills + Windows input

Every boundary ──> JSONL session log ──> replay and evaluation
```

## Environment contract

`reset()` establishes an episode and returns an observation. `observe()` is
side-effect free and requests a visual frame when capture exists.
`observe_without_capture()` supplies telemetry without forcing a new visual
frame. `dispatch(action, command=...)` is the causal execution seam: the runtime
supplies one globally unique command ID and complete based-on revision, and the
receipt binds the result to the later observation. `step(action)` remains the
legacy primitive beneath that seam. `close()` releases resources without
manipulating the game.

## Continuous world-state stream

Only feature-flagged continuous mode creates the in-process
`WorldStateStore`. One cancellable `ObservationPump` reads the environment on a
configured cadence; consumers subscribe to the store rather than independently
polling the telemetry file. Publishing is synchronous within the asyncio event
loop, so validation, registry updates, journal writes, and subscriber fan-out
are one ordered operation.

The store:

- rejects regressing or state-conflicting revisions and reports telemetry
  sequence stalls;
- carries forward the last validated screenshot on telemetry-only updates;
- bounds snapshot history, semantic deltas, event journal, command history, and
  subscriber queues;
- retains transient observation events after the latest snapshot drops them;
- tracks capability epochs without converting unavailable data into absence;
- preserves validated native handle IDs exactly when
  `identity.stable_handles` is present, and otherwise normalizes legacy nearby
  ordinal IDs into process-local lifetime IDs using observed fingerprint and
  position evidence while logging ambiguous matches;
- owns active plan, step, command ID, and causal start/completion revisions;
- provides `wait_for(..., after_revision=R)`, which cannot succeed from `R`.

This is an authoritative Python state stream over the plugin's existing atomic
latest-snapshot file. It is not a native event transport. Native protocol
`0.4.0` supplies session-scoped validated-handle identity, bounded keyed
command acknowledgements, game time, exact dialogue target/options, and current
tooltip/source bounds; older producers still use the portable
ambiguity-aware registry. See
`docs/ADR_WORLD_STATE_STREAM.md` and
`docs/ADR_STABLE_NATIVE_IDENTITY.md`.

Generic strategic output must still match the current exact revision. The sole
live `food_procurement_v1` exception may advance a plan basis across
sequence-only updates after comparing an exact phase fence from the immutable
planner observation to latest state. Any identity, capability, game,
policy-authoritative UI, native command, selection, or exact-target change
rejects the plan; transient capture dimensions are excluded. A successful
rebase is a distinct lifecycle event and does not skip ordinary policy,
precondition, or guard validation.

For this policy, the hosted response chooses only the structurally constrained
phase actions, target, and arguments. After that structure matches the current
phase, trusted code compiles the canonical conditions, linear branches,
timeouts, and risk budgets. This removes duplicated safety boilerplate from
model discretion while retaining the typed plan executor and all immediate
checks.

## Independent safety supervision

Continuous mode starts one `SafetySupervisor` subscriber before the
observation pump. It evaluates deterministic reflexes, telemetry staleness,
consecutive sequence stalls, pause-capability withdrawal, resumed human input,
F12 emergency stop, and unexpected unpause from immutable `StoreUpdate`
snapshots. Live duplicate sequences begin counting only after the configured
telemetry wall age, because the 2 Hz native producer is slower than the Python
observation cadence. Each update carries the active plan and command state that
existed when it was published, so delayed subscriber processing cannot
retroactively reclassify an authorized action.

The scheduler races strategic planning and plan execution against the
supervisor's first latched preemption. A blocked task is canceled once. If
action delivery was already attempted, the executor spends its reservation and
records the command as inconclusive rather than risking an automatic duplicate.
Cleanup uses only `PauseAction(paused=true)`, still passes control-mode and
allowlist policy, and may bypass only the ordinary rate counter so exhaustion
cannot prevent an emergency pause. A cleanup terminal is `safe_paused` only
after a later capable world revision confirms pause; otherwise it is explicitly
failed or unverified.

The Windows controller now reports human input even between its short input
leases and carries F12 into the same supervisor stream. Deterministic tests
cover the preemption semantics; real controller latency still requires
supervised live validation. See `docs/ADR_SAFETY_SUPERVISOR.md`.

## Final input-boundary revalidation

Executor validation happens before `LiveEnvironment` waits for a quiet input
turn, and that polite wait is deliberately unbounded. Each continuous step
therefore carries a bounded `ExecutionToken` (`input_boundary.py`) holding its
plan/step/command identity, control mode, validated revision, plan assumptions,
step preconditions, and a deferred accessor to the world-state store.

Inside the acquired lease — after the calibration recheck and immediately before
the first primitive — the environment re-reads the latest canonical observation
and re-evaluates that authorization through the same `evaluate_conditions`
machinery. A missing observation, regressed revision, changed control mode,
human input, emergency stop, or any non-`true` assumption or precondition emits
zero primitives and returns an `InputBoundaryRejected` receipt, which releases
the reservation through the ordinary definitive-rejection path.

Every token-bearing receipt carries an `InputBoundaryReport`, and the executor
emits `input_boundary_revalidated` or `input_boundary_rejected` with the lease
wait and both revisions. Native-assisted issue-time DLL fences are unchanged and
the boundary is additive. See `docs/ADR_INPUT_BOUNDARY_AUTHORITY.md`.

## Stateful movement options and concurrent patches

In portable continuous mode, a configured movement-pulse `SkillAction` is
adapted into `StatefulMovementOption` instead of remaining an opaque executor
await. The existing macro/environment code still performs the action; the
adapter adds explicit prepared, running, progress, succeeded, failed, and
cancelled state. It polls the shared store, owns one task, and makes repeated
cancellation idempotent.

While that option is active, the executor may give the strategic planner an
immutable observation containing `ActivePlanContext`. Only a `PlanPatch`
matching that plan ID, version, and exact start revision can be staged. The
active or completed step IDs are protected. After the option succeeds, the
executor rebases only the proposed future graph onto the latest revision and
revalidates topology, assumptions, policy, and remaining action/risk/time
budgets. The ordinary guard and precondition checks still run before every
replacement action. Any stale, mismatched, wrong-type, invalid, or late advisory
is logged and discarded.

The active live food profile disables concurrent advisories. Its short
movement pulse completes well before the measured hosted response, while its
accepted phase plan already carries the bounded future dialogue/inspection
steps. Portable/mock profiles retain the concurrency path and its regression
coverage.

Both hosted planners select their structured output type mechanically:
`PlannerDecision` for `single_step`, `PlanEnvelope` for an idle continuous
scheduler, and `PlanPatch` whenever `ActivePlanContext` is present. The
Responses planner also applies a configured base-plus-per-step output-token
budget, capped independently of the strategic timeout. Condition paths are a
closed schema enum; semantic shape, capabilities, revision binding, topology,
and action policy remain application-validated after structured decoding.

This is intentionally an adapter around the proven movement macro, not a
rewrite of live movement control or a general option framework. Live continuous
mode is disabled by default; `food_procurement_v1` is the only policy that may
cross that boundary. See `docs/ADR_STATEFUL_MOVEMENT_OPTIONS.md`.

## Partial observability

Telemetry carries an explicit capability list. The planner must not interpret a
missing field as zero. Exact hidden faction values, distant entities, complete
map data, and mechanical formulas should remain unavailable unless the player
could reasonably observe them.

## Action hierarchy

Primitives are pause, speed, wait, key, hotkey, cursor move, and click. Skills
expand into bounded primitive sequences. In `single_step` the LLM chooses one
action; in `continuous` it may propose a bounded typed plan. It never
micromanages primitive input timing. Reflexes may pause or stop, but broad
autonomy stays with the planner.

Every run has a typed control mode. `interface_only` is the default and filters
native command capabilities and marked skills before planning; the guard and
environment reject them again at execution boundaries. `native_assisted`
requires a configuration opt-in plus a dedicated CLI acknowledgement before
live execution. Observations, receipts, lifecycle events, overlays, summaries,
and metrics retain the mode.

## Native boundary

The plugin owns no model logic. Its observational path serializes a versioned,
partial snapshot at a low fixed frequency. It hooks a known main/UI-thread
update point, calls the original function, samples only validated fields, and
writes an atomic file. The Python process never loads Kenshi memory directly.

The plugin also contains one reviewed `PLAYER_TALK_TO` command bridge used by
`approach_confirmed_vendor`. This is not described as read-only or UI-only. It
is marked `requires_native_assisted` in the macro schema and unavailable in the
default mode. Python atomically writes one strict request before the bridge
hotkey. The plugin accepts only the exact caller command ID, world-revision
sequence, native mode, identity session, one-character selection, and stable
vendor target. A bounded acknowledgement ring reports rejection reasons,
acceptance, exact-dialogue completion, and selection/target cancellation.
See `docs/ADR_CONTROL_MODES.md` and
`docs/ADR_CAUSAL_NATIVE_COMMANDS.md`.

## Failure attribution

Logs distinguish observation errors, planner errors, policy rejection, input
execution, and observed outcome. A benchmark result should therefore say
whether the agent misunderstood the world, chose poorly, failed to operate the
UI, or lacked sufficient telemetry.
