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
  │                              ├─ latest + valued deltas/events
  │                              ├─ entity lifetimes
  │                              ├─ active plan/command
  │                              └─ subscriber queues
  │                                      ├─> safety supervisor
  │                                      │     └─ cancel + guarded safe pause
  │                                      └─> scheduler/executor
  │                                             ├─ semantic action contracts
  │                                             └─ monitored movement options
  │                                                   ↕ future-only advisory
  ├─ reflex layer (shared deterministic pause/stop rules)
  ├─ planner (heuristic, scripted, subprocess, or vision LLM)
  ├─ schema + policy + rate-limit guard
  ├─ skill/macro compiler
  └─ executor
       ├─ interface_only ──> Windows SendInput ──> ordinary Kenshi UI
       └─ native_assisted ──> marked bounded bridge skills + Windows input

Every boundary ──> JSONL session log ──> lifecycle analysis and evaluation
```

Full environment replay is a separate logging level. Default compact
observation digests keep long live logs bounded and support lifecycle analysis,
but `ReplayEnvironment` requires
`runtime.log_full_observations: true`.

## Environment contract

`reset()` establishes an episode and returns an observation. `observe()` is
side-effect free and requests a visual frame when capture exists.
`observe_without_capture()` supplies telemetry without forcing a new visual
frame. `dispatch(action, command=...)` is the causal execution seam: the runtime
supplies one globally unique command ID and complete based-on revision, and the
receipt binds the result to the later observation. `step(action)` remains the
legacy primitive beneath that seam. `close()` releases resources without
manipulating the game. Consequently, the safety supervisor's verified pause
cleanup does not currently extend to normal stop, budget exhaustion,
cancellation, exception, or objective-completion exits; a unified final-state
owner remains open work.

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

This is an authoritative Python state stream over the plugin's atomic
latest-snapshot file, not a native event transport. Native protocol `0.6.0`
supplies session-scoped validated-handle identity, bounded keyed command
acknowledgements, squad/inventory facts, game time, dialogue and management UI,
tooltip/source bounds, named item cells, and visible controls. Older producers
still use the portable ambiguity-aware registry. See
`docs/ADR_WORLD_STATE_STREAM.md` and
`docs/ADR_STABLE_NATIVE_IDENTITY.md`.

Portable generic strategic output must match the current exact revision. The
live `dialogue_interaction_v1` policy may rebase a plan that aged during a
hosted call only while the immutable planner observation and latest
observation still authorize every contracted reference, assumption, control
mode, and capability. A successful rebase changes only the plan basis; each
step still binds when reached, passes policy and budget validation, and is
rebound inside the input lease. The policy name is historical: it now
validates the generic semantic action catalog and prescribes no dialogue,
vendor, or food sequence.

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
cover the preemption semantics, and supervised live runs have exercised human
handback and confirmed pause. Repeated F12/focus/controller-latency trials
remain open. See `docs/ADR_SAFETY_SUPERVISOR.md`.

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

## Calibration identity

A profile-calibrated pointer click depends on more than client size, so
`CalibrationIdentity` models the full set of facts it needs — client size,
window mode, UI scale, DPI transform, keymap, and profile/macro hashes — each
nullable. `LiveEnvironment.classify_pointer_action` sorts each action into
coordinate-independent, semantic-current, profile-calibrated, or unsupported;
only profile-calibrated actions require a match. `evaluate_calibration_identity`
compares the fields the profile declares against what the controller observes
and returns `not_required`, `matched`, `mismatched`, or `unknown` — a declared
field the host cannot read is `unknown` and blocks input, never a silent match.
The report rides on every pointer receipt and, via the `ExecutionToken`, is
re-checked inside the input lease by the P3 boundary. Only client width and
height are observable today; the other fields are modelled and enforced but
await controller support. See `docs/ADR_CALIBRATION_IDENTITY.md`.

## Stateful movement options and concurrent patches

Configured movement-pulse skills can be adapted into a
`StatefulMovementOption` instead of remaining opaque executor awaits. Contracted
`approach_dialogue_target` always routes through `StatefulApproachOption`, which
owns one native order, progress monitoring, adoption of an already-active exact
order, arrival/dialogue success, target/threat/timeout failure, and idempotent
cancellation. In an unpaused continuous profile it monitors ordinary world
progress; in stop-motion profiles it owns bounded unpause/re-pause pulses.

`move_in_direction` is declared with the same `MONITORED_OPTION` execution
kind, but the current adapter extracts a nonempty `target_id` before creating
an option. The targetless action therefore does not receive the ownership this
architecture intends. It must not be described as a working generalized option
until that mismatch and the native wire mismatch are fixed end to end.

While that option is active, the executor may give the strategic planner an
immutable observation containing `ActivePlanContext`. Only a `PlanPatch`
matching that plan ID, version, and exact start revision can be staged. The
active or completed step IDs are protected. After the option succeeds, the
executor rebases only the proposed future graph onto the latest revision and
revalidates topology, assumptions, policy, and remaining action/risk/time
budgets. The ordinary guard and precondition checks still run before every
replacement action. Any stale, mismatched, wrong-type, invalid, or late advisory
is logged and discarded.

The long-form live profile enables concurrent option planning so useful
movement can overlap one strategic future-plan advisory. Staging never changes
the running option, and a patch still cannot execute until the option terminates
and latest-state/budget validation passes.

Both hosted planners select their structured output type mechanically:
`PlannerDecision` for `single_step`, `PlanEnvelope` for an idle continuous
scheduler, and `PlanPatch` whenever `ActivePlanContext` is present. The
Responses planner also applies a configured base-plus-per-step output-token
budget, capped independently of the strategic timeout. Condition paths are a
closed schema enum; semantic shape, capabilities, revision binding, topology,
and action policy remain application-validated after structured decoding.

This remains a bounded adapter rather than a general option framework.
Live-continuous execution is disabled in the default profiles and requires an
implemented policy plus its separate acknowledgement. The current implemented
live policy is `dialogue_interaction_v1`. See
`docs/ADR_STATEFUL_MOVEMENT_OPTIONS.md`.

## Partial observability

Telemetry carries an explicit capability list. The planner must not interpret a
missing field as zero. Exact hidden faction values, distant entities, complete
map data, and mechanical formulas should remain unavailable unless the player
could reasonably observe them.

## Action hierarchy

Raw keys, hotkeys, cursor moves, clicks, and scrolls are controller primitives;
the generic live planner cannot author them. Run control is separately typed as
noop, wait, pause/speed, and whole-run stop. Reusable semantic actions bind
current telemetry references through one catalog:

- approach a dialogue target;
- move to a nearby character; a bounded bearing/distance action is declared but
  currently blocked at its targetless wire/option boundary;
- activate a visible control or dismiss the current screen;
- buy, sell, equip, or scroll one current window;
- press one allowlisted reversible Kenshi game binding.

Each `ActionContract` owns planner visibility, capability/control-mode
requirements, pointer class, native requirement, risk cost, idempotency,
reference binding, execution route, receipt kind, and required verification
paths. The planner may compose these actions in a bounded continuous plan; it
never micromanages primitive timing or coordinates. Legacy skills still expand
into bounded primitives for compatibility and calibrated transport.

This is the intended single source of action truth, not yet complete executable
truth. In particular, most contracts name no controller-owned effect predicate:
the generic policy requires a model-authored condition and evaluates it only on
a causally later revision. That proves temporal ordering, not necessarily that
the dispatched action caused the intended transition. The world-state store's
before/after deltas and native acknowledgements are the substrate for the
missing effect engine.

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

The plugin also contains three reviewed native-assisted commands behind one
strict request bridge:

- `approach_confirmed_vendor`, a legacy wire name that now accepts any exact
  valid dialogue target and completes only on exact-target dialogue;
- `move_to_character`, which follows one exact nearby character and completes
  on arrival without opening dialogue;
- `move_in_direction`, whose intended native handler walks a bounded
  bearing/distance from the selected character. The current Python producer
  sends the targetless shape the model permits, while the C++ parser and Python
  acknowledgement model still require a nonempty target ID; the executor's
  option adapter has the same target assumption. It is therefore not currently
  accepted end to end.

Python atomically writes one request before the private bridge hotkey. The
plugin accepts only the exact caller command ID, current world-revision
sequence, native mode, identity session, one-character selection, and exact
target fields. Bounded direction fields are parsed and handled later in the
native path, but the shared parser's earlier nonempty-target check currently
prevents a targetless directional request from reaching that branch. A bounded
acknowledgement ring reports
rejection, acceptance, completion/cancellation, and terminal sequences. These
commands are unavailable in `interface_only`; the DLL is not described as
globally read-only.
See `docs/ADR_CONTROL_MODES.md` and
`docs/ADR_CAUSAL_NATIVE_COMMANDS.md`.

Documentation follows [the truth policy](docs/DOCUMENTATION_TRUTH.md): current
state, enduring design, generated contracts, dated live evidence, and historical
ledger entries have separate roles.

## Failure attribution

Logs distinguish observation errors, planner errors, policy rejection, input
execution, and observed outcome. A benchmark result should therefore say
whether the agent misunderstood the world, chose poorly, failed to operate the
UI, or lacked sufficient telemetry.
